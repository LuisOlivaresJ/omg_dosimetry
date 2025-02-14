# -*- coding: utf-8 -*-
"""
The dose analysis module performs in-depth comparison from film dose to reference dose image from treatment planning system.

Features:
    - Perform registration by identifying fiducial markers to set isocenter
    - Interactive display of analysis results (gamma map, relative error, dose profiles)
    - Gammap analysis: display gamma map, pass rate, histogram, pass rate vs dose bar graph,
      pass rate vs distance to agreement (fixed dose to agreement),
      pass rate vs dose to agreement (fixed distance to agreement)
    - Publish PDF report
    
Requirements:
    This module is built as an extension to pylinac package.
    Tested with pylinac 2.0.0, which is compatible with python 3.5.
    
Written by Jean-Francois Cabana
Version 2023-03-08
"""

import numpy as np
import scipy.ndimage.filters as spf
import copy
import matplotlib
import matplotlib.pyplot as plt
import os
from pylinac.core.utilities import is_close
import math
from scipy.signal import medfilt
import pickle
from pylinac.core import pdf
import io
from pathlib import Path
import pymedphys
from matplotlib.widgets  import RectangleSelector, MultiCursor, Cursor
import webbrowser
from .imageRGB import load, ArrayImage, equate_images
import bz2
import time

class DoseAnalysis(): 
    
    def __init__(self, film_dose=None, ref_dose=None, film_dose_factor=1, ref_dose_factor=1, flipLR=False, flipUD=False, rot90=0, ref_dose_sum=False): 
        if film_dose is not None: self.film_dose = load(film_dose)
        if rot90: self.film_dose.array = np.rot90(self.film_dose.array, k=rot90)
        if flipLR: self.film_dose.array = np.fliplr(self.film_dose.array)
        if flipUD: self.film_dose.array = np.flipud(self.film_dose.array)
        if ref_dose is None: self.ref_dose = None
            
        if ref_dose is not None:
            # If need to add multiple plane dose images, assume all images in folder given by ref_dose
            if ref_dose_sum:
                files = os.listdir(ref_dose)
                img_list = []
                for file in files: 
                    img_file = os.path.join(ref_dose, file)
                    filebase, fileext = os.path.splitext(file)    
                    if file == 'Thumbs.db': continue
                    if os.path.isdir(img_file): continue       
                    img_list.append(load(img_file))    
                self.ref_dose = img_list[0]
                new_array = np.stack(tuple(img.array for img in img_list), axis=-1)
                self.ref_dose.array = np.sum(new_array, axis=-1) 
            else: self.ref_dose = load(ref_dose)
  
        self.apply_film_factor(film_dose_factor = film_dose_factor)
        self.apply_ref_factor(ref_dose_factor = ref_dose_factor)

    def apply_film_factor(self, film_dose_factor = None):
        """ Apply a normalisation factor to film dose. """
        if film_dose_factor is not None:
            self.film_dose_factor = film_dose_factor
            self.film_dose.array = self.film_dose.array * self.film_dose_factor
            print("Applied film normalisation factor = {}".format(self.film_dose_factor))

    def apply_ref_factor(self, ref_dose_factor = None):
        """ Apply a normalisation factor to reference dose. """
        if ref_dose_factor is not None:
            self.ref_dose_factor = ref_dose_factor
            self.ref_dose.array = self.ref_dose.array * self.ref_dose_factor
            print("Applied ref dose normalisation factor = {}".format(self.ref_dose_factor))

    def apply_factor_from_isodose(self, norm_isodose = 0):
        """ Apply film normalisation factor from a reference dose isodose. """
        print("Computing normalisation factor from doses > {} cGy.".format(norm_isodose))
        self.norm_dose = norm_isodose        
        indices = np.where(self.ref_dose.array > self.norm_dose)
        mean_ref = np.mean(self.ref_dose.array[indices])
        mean_film = np.mean(self.film_dose.array[indices])          
        self.apply_film_factor(film_dose_factor = mean_ref / mean_film )
        
    def apply_factor_from_roi(self, norm_dose = None):
        """ Apply film normalisation factor from a rectangle ROI. """
        
        self.norm_dose = norm_dose      
        msg = 'Factor from ROI: Click and drag to draw an ROI manually. Press ''enter'' when finished.'
        self.roi_xmin, self.roi_xmax = [], []
        self.roi_ymin, self.roi_ymax = [], []

        self.fig = plt.figure()
        ax = plt.gca()  
        self.film_dose.plot(ax=ax)  
        ax.plot((0,self.film_dose.shape[1]),(self.film_dose.center.y,self.film_dose.center.y),'k--')
        ax.set_xlim(0, self.film_dose.shape[1])
        ax.set_ylim(self.film_dose.shape[0],0)
        ax.set_title(msg)
        print(msg)
        
        def select_box(eclick, erelease):
            x1, y1 = int(eclick.xdata), int(eclick.ydata)
            x2, y2 = int(erelease.xdata), int(erelease.ydata)
            self.roi_xmin, self.roi_xmax = min(x1,x2), max(x1,x2)
            self.roi_ymin, self.roi_ymax = min(y1,y2), max(y1,y2)
        
        self.rs = RectangleSelector(ax, select_box, useblit=True, button=[1], minspanx=5, minspany=5, spancoords='pixels', interactive=True)  
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.apply_factor_from_roi_press_enter)
        self.wait = True
        while self.wait: plt.pause(5)
        return
        
    def apply_factor_from_roi_press_enter(self, event):
        """ Continue when ''enter'' is pressed. """      
        if event.key == 'enter':
            roi_film = np.median(self.film_dose.array[self.roi_ymin:self.roi_ymax, self.roi_xmin:self.roi_xmax])
            
            if self.norm_dose is None:  # If no normalisation dose is given, assume we normalisation on ref_dose
                roi_ref = np.median(self.ref_dose.array[self.roi_ymin:self.roi_ymax, self.roi_xmin:self.roi_xmax])
                factor = roi_ref/roi_film
                print("Median film dose = {} cGy; median ref dose = {} cGy".format(roi_film, roi_ref))
                
            else: factor = self.norm_dose / roi_film            
            self.apply_film_factor(film_dose_factor = factor)
            
            if hasattr(self, "rs"): del self.rs                
            self.fig.canvas.mpl_disconnect(self.cid)
            plt.close(self.fig)   
            self.wait = False
            return

    def apply_factor_from_norm_film(self, norm_dose = None, norm_roi_size = 10):
        """ Define an ROI of norm_roi_size mm x norm_roi_size mm to compute dose factor from normalisation film. """
        
        self.norm_dose = norm_dose
        self.norm_roi_size = norm_roi_size
        msg = 'Factor from normalisation film: Double-click at the center of the film markers. Press enter when done'
        self.roi_center = []
        self.roi_xmin, self.roi_xmax = [], []
        self.roi_ymin, self.roi_ymax = [], []
        
        self.fig = plt.figure()
        ax = plt.gca()  
        self.film_dose.plot(ax=ax)  
        ax.plot((0,self.film_dose.shape[1]),(self.film_dose.center.y,self.film_dose.center.y),'k--')
        ax.set_xlim(0, self.film_dose.shape[1])
        ax.set_ylim(self.film_dose.shape[0],0)
        ax.set_title(msg)
        print(msg)
        
        self.fig.canvas.mpl_connect('button_press_event', self.onclick_norm)
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.apply_factor_from_roi_press_enter)         
        self.wait = True
        while self.wait: plt.pause(5)
            
    def onclick_norm(self, event):
        ax = plt.gca()
        if event.dblclick:
            size_px = self.norm_roi_size * self.film_dose.dpmm / 2
            self.roi_center = ([int(event.xdata), int(event.ydata)])
            self.roi_xmin, self.roi_xmax = int(event.xdata) - size_px, int(event.xdata) + size_px
            self.roi_ymin, self.roi_ymax = int(event.ydata) - size_px, int(event.ydata) + size_px
            
            rect = plt.Rectangle( (min(self.roi_xmin,self.roi_xmax),min(self.roi_ymin,self.roi_ymax)), np.abs(self.roi_xmin-self.roi_xmax), np.abs(self.roi_ymin-self.roi_ymax), fill=False )
            ax.add_patch(rect)    
            ax.plot((self.roi_center[0]-size_px,self.roi_center[0]+size_px),(self.roi_center[1],self.roi_center[1]),'w', linewidth=2)
            ax.plot((self.roi_center[0],self.roi_center[0]),(self.roi_center[1]-size_px,self.roi_center[1]+size_px),'w', linewidth=2)
            plt.gcf().canvas.draw_idle()

    def crop_film(self):
        """ Define an ROI to crop film. """     
        msg = 'Crop film: Click and drag to draw an ROI. Press ''enter'' when finished.'
        
        self.fig = plt.figure()
        ax = plt.gca()  
        self.film_dose.plot(ax=ax)  
        ax.plot((0,self.film_dose.shape[1]),(self.film_dose.center.y,self.film_dose.center.y),'k--')
        ax.set_xlim(0, self.film_dose.shape[1])
        ax.set_ylim(self.film_dose.shape[0],0)
        ax.set_title(msg)
        print(msg)
        
        def select_box(eclick, erelease):
            x1, y1 = int(eclick.xdata), int(eclick.ydata)
            x2, y2 = int(erelease.xdata), int(erelease.ydata)   
            self.roi_xmin, self.roi_xmax = min(x1,x2), max(x1,x2)
            self.roi_ymin, self.roi_ymax = min(y1,y2), max(y1,y2)

        self.rs = RectangleSelector(ax, select_box, useblit=True, button=[1], minspanx=5, minspany=5, spancoords='pixels', interactive=True)  
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.crop_film_press_enter)
        self.wait = True
        while self.wait: plt.pause(5)
        return
        
    def crop_film_press_enter(self, event):
        """ Continue when ''enter'' is pressed. """      
        if event.key == 'enter':
            del self.rs                
            left = self.roi_xmin
            right = self.film_dose.shape[1] - self.roi_xmax
            top = self.roi_ymin
            bottom = self.film_dose.shape[0] - self.roi_ymax    
            self.film_dose.crop(left,'left')
            self.film_dose.crop(right,'right')
            self.film_dose.crop(top,'top')
            self.film_dose.crop(bottom,'bottom')  
            
            self.fig.canvas.mpl_disconnect(self.cid)
            plt.close(self.fig)   
            self.wait = False
            return
        
    def gamma_analysis(self, film_filt=0, doseTA=3.0, distTA=3.0, threshold=0.1, norm_val='max', local_gamma=False, max_gamma=None, random_subset=None):
        """ Perform Gamma analysis """
        self.doseTA, self.distTA = doseTA, distTA
        self.film_filt, self.threshold, self.norm_val = film_filt, threshold, norm_val        
        start_time = time.time()
        self.GammaMap = self.computeGamma(doseTA=doseTA, distTA=distTA, threshold=threshold, norm_val=norm_val, local_gamma=local_gamma, max_gamma=max_gamma, random_subset=random_subset)       
        print("--- Done! ({:.1f} seconds) ---".format((time.time() - start_time)))
        self.computeDiff()
    
    def computeHDmedianDiff(self, threshold=0.8, ref = 'max'):
        """ Compute median difference between film and reference doses in high dose region. """
        if ref == 'max': HDthreshold = threshold * self.ref_dose.array.max()
        else:  HDthreshold = threshold * ref
        film_HD = self.film_dose.array[self.ref_dose.array > HDthreshold]
        ref_HD = self.ref_dose.array[self.ref_dose.array > HDthreshold]
        self.HD_median_diff = np.median((film_HD-ref_HD)/ref_HD) * 100
        return self.HD_median_diff
            
    def computeDiff(self):
        """ Compute the difference map with the reference image.
            Returns self.DiffMap = film_dose - ref_dose """
        self.DiffMap = ArrayImage(self.film_dose.array - self.ref_dose.array, dpi=self.film_dose.dpi)
        self.RelError = ArrayImage(100*(self.film_dose.array - self.ref_dose.array)/self.ref_dose.array, dpi=self.film_dose.dpi)
        self.DiffMap.MSE =  sum(sum(self.DiffMap.array**2)) / len(self.film_dose.array[(self.film_dose.array > 0)]) 
        self.DiffMap.RMSE = self.DiffMap.MSE**0.5    
    
    def computeGamma(self, doseTA=2, distTA=2, threshold=0.1, norm_val=None, local_gamma=False, max_gamma=None, random_subset=None):
        """Comput Gamma (using pymedphys.gamma) """
        print("Computing {}% {} mm Gamma...".format(doseTA, distTA))
#       # error checking
        if not is_close(self.film_dose.dpi, self.ref_dose.dpi, delta=3):
            raise AttributeError("The image DPIs to not match: {:.2f} vs. {:.2f}".format(self.film_dose.dpi, self.ref_dose.dpi))
        same_x = is_close(self.film_dose.shape[1], self.ref_dose.shape[1], delta=1.1)
        same_y = is_close(self.film_dose.shape[0], self.ref_dose.shape[0], delta=1.1)
        if not (same_x and same_y):
            raise AttributeError("The images are not the same size: {} vs. {}".format(self.film_dose.shape, self.ref_dose.shape))

        # set up reference and comparison images
        film_dose, ref_dose = ArrayImage(copy.copy(self.film_dose.array)), ArrayImage(copy.copy(self.ref_dose.array))
        
        if self.film_filt:
            film_dose.array = medfilt(film_dose.array, kernel_size=(self.film_filt, self.film_filt))

        if norm_val is not None:
            if norm_val == 'max': norm_val = ref_dose.array.max()
            film_dose.normalize(norm_val)
            ref_dose.normalize(norm_val)

        # set coordinates [mm]
        x_coord = (np.array(range(0, self.ref_dose.shape[0])) / self.ref_dose.dpmm - self.ref_dose.physical_shape[0]/2).tolist()
        y_coord = (np.array(range(0, self.ref_dose.shape[1])) / self.ref_dose.dpmm - self.ref_dose.physical_shape[1]/2).tolist()
        axes_reference, axes_evaluation = (x_coord, y_coord), (x_coord, y_coord)
        dose_reference, dose_evaluation = ref_dose.array, film_dose.array

        # set film_dose = 0 to Nan to avoid computing on padded pixels
        dose_evaluation[dose_evaluation == 0] = 'nan'
        
        # Compute the number of pixels to analyze
        if random_subset:
            random_subset = int(len(dose_reference[dose_reference >= threshold].flat) * random_subset)
        
        # Gamma computation and set maps
        gamma = pymedphys.gamma(axes_reference, dose_reference, axes_evaluation, dose_evaluation, doseTA, distTA, threshold*100,
                                local_gamma=local_gamma, interp_fraction=10, max_gamma=max_gamma, random_subset=random_subset)
        GammaMap = ArrayImage(gamma, dpi=film_dose.dpi)
              
        fail = np.zeros(GammaMap.shape)
        fail[(GammaMap.array > 1.0)] = 1
        GammaMap.fail = ArrayImage(fail, dpi=film_dose.dpi)
        
        passed = np.zeros(GammaMap.shape)
        passed[(GammaMap.array <= 1.0)] = 1
        GammaMap.passed = ArrayImage(passed, dpi=film_dose.dpi)
        
        GammaMap.npassed = sum(sum(passed == 1))
        GammaMap.nfail = sum(sum(fail == 1))
        GammaMap.npixel = GammaMap.npassed + GammaMap.nfail
        GammaMap.passRate = GammaMap.npassed / GammaMap.npixel * 100
        GammaMap.mean = np.nanmean(GammaMap.array)
        
        return GammaMap
                    
    def plot_gamma_varDoseTA(self, ax=None, start=0.5, stop=4, step=0.5): 
        """ Plot graph of Gamma pass rate vs variable doseTA """
        distTA, threshold, norm_val = self.distTA, self.threshold, self.norm_val
        values = np.arange(start,stop,step)
        GammaVarDoseTA = np.zeros((len(values),2))

        i=0
        for value in values:
            gamma = self.computeGamma(doseTA=value, distTA=distTA, threshold=threshold, norm_val=norm_val)
            GammaVarDoseTA[i,0] = value
            GammaVarDoseTA[i,1] = gamma.passRate
            i=i+1
        
        if ax is None: fig, ax = plt.subplots()
        x, y = GammaVarDoseTA[:,0], GammaVarDoseTA[:,1]
        ax.plot(x,y,'o-')
        ax.set_title('Variable Dose TA, Dist TA = {} mm'.format(distTA))
        ax.set_xlabel('Dose TA (%)')
        ax.set_ylabel('Gamma pass rate (%)')
        
    def plot_gamma_varDistTA(self, ax=None, start=0.5, stop=4, step=0.5): 
        """ Plot graph of Gamma pass rate vs variable distTA """
        doseTA = self.doseTA
        threshold = self.threshold
        norm_val = self.norm_val
        
        values = np.arange(start,stop,step)
        GammaVarDistTA = np.zeros((len(values),2))
        
        i=0
        for value in values:
            gamma = self.computeGamma(doseTA=doseTA, distTA=value, threshold=threshold, norm_val=norm_val)
            GammaVarDistTA[i,0] = value
            GammaVarDistTA[i,1] = gamma.passRate
            i=i+1
        
        x = GammaVarDistTA[:,0]
        y = GammaVarDistTA[:,1]
        if ax is None:
            fig, ax = plt.subplots()
        ax.plot(x,y,'o-')
        ax.set_title('Variable Dist TA, Dose TA = {} %'.format(doseTA))
        ax.set_xlabel('Dist TA (mm)')
        ax.set_ylabel('Gamma pass rate (%)')      
        
    def plot_gamma_hist(self, ax=None, bins='auto', range=[0,3]):
        if ax is None:
            fig, ax = plt.subplots()
        ax.hist(self.GammaMap.array[np.isfinite(self.GammaMap.array)], bins=bins, range=range)
        ax.set_xlabel('Gamma value')
        ax.set_ylabel('Pixels count')
        ax.set_title("Gamma map histogram")
        
    def plot_gamma_pass_hist(self, ax=None, bin_size = 50):
        if ax is None:
            fig, ax = plt.subplots()
        analyzed = np.isfinite(self.GammaMap.array)
        bins = np.arange(0, self.ref_dose.array.max()+bin_size, bin_size)
        dose = self.ref_dose.array[analyzed]
        gamma_pass = self.GammaMap.passed.array[analyzed]   # analyzed array includes failing gamma points
        dose_pass = (gamma_pass * dose)
        dose_pass = dose_pass[dose_pass > 0]     # Remove failing gamma points (value 0 from self.GammaMap.passed.array)
        dose_hist = np.histogram(dose, bins=bins)
        dose_pass_hist = np.histogram(dose_pass, bins=bins)
        dose_pass_rel = np.zeros(len(dose_pass_hist[0]))
        
        for i in range(0,len(dose_pass_hist[0])):
            if dose_hist[0][i] > 0:
                dose_pass_rel[i] = float(dose_pass_hist[0][i]) / float(dose_hist[0][i]) * 100
        
        ax.bar(bins[:-1], dose_pass_rel, width=bin_size,  align='edge', linewidth=1, edgecolor='k')
        ax.set_xlabel('Doses (cGy)')
        ax.set_ylabel('Pass rate (%)')
        ax.set_title("Gamma pass rate vs dose")
        ax.set_xticks(bins)
        
    def plot_gamma_stats(self, figsize=(10, 10), show_hist=True, show_pass_hist=True, show_varDistTA=True, show_var_DoseTA=True):
        fig, ((ax1,ax2),(ax3,ax4)) = plt.subplots(2,2, figsize=figsize)
        
        axes = (ax1,ax2,ax3,ax4)
        i = 0
        
        if show_hist:
            self.plot_gamma_hist(ax=axes[i])
            i=i+1
        if show_pass_hist:
            self.plot_gamma_pass_hist(ax=axes[i])
            i=i+1
        if show_varDistTA:
            self.plot_gamma_varDistTA(ax=axes[i])
            i=i+1
        if show_var_DoseTA:
            self.plot_gamma_varDoseTA(ax=axes[i])
        
    def plot_profile(self, ax=None, profile='x', position=None, title=None, diff=False, offset=0):
        film = self.film_dose.array
        ref = self.ref_dose.array
        if profile == 'x':
            if position is None:
                position = np.floor(self.ref_dose.shape[0] / 2).astype(int)
            film_prof = film[position,:]
            ref_prof = ref[position,:]
            x_axis = (np.array(range(0, len(film_prof))) / self.film_dose.dpmm).tolist()
        elif profile == 'y':
            if position is None:
                position = np.floor(self.ref_dose.shape[1] / 2).astype(int)
            film_prof = film[:,position]
            ref_prof = ref[:,position]
            x_axis = (np.array(range(0, len(film_prof))) / self.film_dose.dpmm).tolist()
        
        if ax is None:
            fig, ax = plt.subplots()    
        ax.clear()
        ax.plot([i+offset for i in x_axis], film_prof,'r-', linewidth=2)
        ax.plot(x_axis, ref_prof,'b--', linewidth=2)
        
        if title is None:
            if profile == 'x': title='Profile horizontal (y={})'.format(position)
            if profile == 'y': title='Profile vertical (x={})'.format(position)
        ax.set_title(title)
        ax.set_xlabel('Position (mm)')
        ax.set_ylabel('Dose (cGy)')
        
        if diff:
            diff_prof = film_prof - ref_prof
            ax.plot(x_axis, diff_prof,'g-', linewidth=2)
            
    
    def show_results(self, fig=None, x=None, y=None):       
        a = None
        if x is None:
            x = np.floor(self.ref_dose.shape[1] / 2).astype(int)
        elif x == 'max':
            a = np.unravel_index(self.ref_dose.array.argmax(), self.ref_dose.array.shape)
            x = a[1]
        if y is None:
            y = np.floor(self.ref_dose.shape[0] / 2).astype(int)
        elif y == 'max':
            if a is None:
                a = np.unravel_index(self.ref_dose.array.argmax(), self.ref_dose.array.shape)
            y = a[0]
         
        fig, ((ax1,ax2),(ax3,ax4),(ax5,ax6)) = plt.subplots(3,2, figsize=(10, 8))
        fig.tight_layout()
        axes = [ax1,ax2,ax3,ax4,ax5,ax6]
        max_dose_comp = np.percentile(self.ref_dose.array,[98])[0].round(decimals=-1)
        clim = [0, max_dose_comp]  

        self.film_dose.plotCB(ax1, clim=clim, title='Film dose')
        self.ref_dose.plotCB(ax2, clim=clim, title='Reference dose')
        self.GammaMap.plotCB(ax3, clim=[0,2], cmap='bwr', title='Gamma map ({:.2f}% pass; {:.2f} mean)'.format(self.GammaMap.passRate, self.GammaMap.mean))
        ax3.set_facecolor('k')
        min_value = max(-20, np.percentile(self.DiffMap.array,[1])[0].round(decimals=0))
        max_value = min(20, np.percentile(self.DiffMap.array,[99])[0].round(decimals=0))
        clim = [min_value, max_value]    
        self.RelError.plotCB(ax4, cmap='jet', clim=clim, title='Relative Error (%) (RMSE={:.2f})'.format(self.DiffMap.RMSE))
        self.show_profiles(axes, x=x, y=y)
        
        fig.canvas.mpl_connect('button_press_event', lambda event: self.set_profile(event, axes))
        #fig.canvas.mpl_connect('motion_notify_event', lambda event: self.moved_and_pressed(event, axes))
        
    def show_profiles(self, axes, x, y):
        self.plot_profile(ax=axes[-2], profile='x', title='Horizontal profile (y={})'.format(y), position=y)
        self.plot_profile(ax=axes[-1], profile='y', title='Vertical profile (x={})'.format(x), position=x)
        
        for i in range(0,4):
            ax = axes[i]
            while len(ax.lines) > 0:
                ax.lines[-1].remove()
            ax.plot((x,x),(0,self.ref_dose.shape[0]),'w--', linewidth=1)
            ax.plot((0,self.ref_dose.shape[1]),(y,y),'w--', linewidth=1) 
        plt.multi = MultiCursor(None, (axes[0],axes[1],axes[2],axes[3]), color='r', lw=1, horizOn=True)
        plt.show()
        
    def set_profile(self, event, axes):
        # Only clicks inside this axis are valid.
        try: # use try/except in case we are not using Qt backend
            zooming_panning = ( plt.gcf().canvas.cursor().shape() != 0 ) # 0 is the arrow, which means we are not zooming or panning.
        except:
            zooming_panning = False
        if zooming_panning: 
            return
        if event.inaxes in axes[0:4]:
            if event.button == 1:
                x = int(event.xdata)
                y = int(event.ydata)
                self.show_profiles(axes,x=x, y=y)
                plt.gcf().canvas.draw_idle()
        
    def moved_and_pressed(self, event, axes):
        if event.button==1:
            self.set_profile(event, axes)  
        
    def register(self, shift_x=0, shift_y=0, threshold=10, register_using_gradient=False, markers_center=None, rot=0):
        self.register_using_gradient = register_using_gradient
        self.shifts = [shift_x, shift_y]
        self.rot = rot
        self.markers_center = markers_center
        if threshold > 0 :
            self.film_dose.crop_edges(threshold=threshold)
        print('Please double-click on each marker. Press ''enter'' when done')
        print('Keyboard shortcuts: Right arrow = Rotate 90 degrees; Left arrow = Flip horizontally; Up arrow = Flip vertically')
        self.film_dose.plot()
        self.select_markers()
        
    def select_markers(self):
        self.fig = plt.gcf()
        self.markers = []
        ax = plt.gca()
        ax.set_title('Marker 1 =  ; Marker 2 =  ; Marker 3 =  ; Marker 4 =  ')
        self.fig.canvas.mpl_connect('button_press_event', self.onclick)
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.ontype)
        cursor = Cursor(ax, useblit=True, color='white', linewidth=1)
        plt.show()
        
        self.wait = True
        while self.wait: plt.pause(5)
        
    def onclick(self, event):
        ax = plt.gca()
        if event.dblclick:
            l = 20
            self.markers.append([int(event.xdata), int(event.ydata)])
            if len(self.markers)==1:
                ax.plot((self.markers[0][0]-l,self.markers[0][0]+l),(self.markers[0][1],self.markers[0][1]),'w', linewidth=1)
                ax.plot((self.markers[0][0],self.markers[0][0]),(self.markers[0][1]-l,self.markers[0][1]+l),'w', linewidth=1)
                ax.set_title('Marker 1 = {}; Marker 2 =  ; Marker 3 =  ; Marker 4 =  '.format(self.markers[0]))
            if len(self.markers)==2:
                ax.plot((self.markers[1][0]-l,self.markers[1][0]+l),(self.markers[1][1],self.markers[1][1]),'w', linewidth=1)
                ax.plot((self.markers[1][0],self.markers[1][0]),(self.markers[1][1]-l,self.markers[1][1]+l),'w', linewidth=1)
                ax.set_title('Marker 1 = {}; Marker 2 = {}; Marker 3 =  ; Marker 4 =  '.format(self.markers[0], self.markers[1]))
            if len(self.markers)==3:
                ax.plot((self.markers[2][0]-l,self.markers[2][0]+l),(self.markers[2][1],self.markers[2][1]),'w', linewidth=1)
                ax.plot((self.markers[2][0],self.markers[2][0]),(self.markers[2][1]-l,self.markers[2][1]+l),'w', linewidth=1)
                ax.set_title('Marker 1 = {}; Marker 2 = {}; Marker 3 = {}; Marker 4 =  '.format(self.markers[0], self.markers[1], self.markers[2]))
            if len(self.markers)==4:
                ax.plot((self.markers[3][0]-l,self.markers[3][0]+l),(self.markers[3][1],self.markers[3][1]),'w', linewidth=1)
                ax.plot((self.markers[3][0],self.markers[3][0]),(self.markers[3][1]-l,self.markers[3][1]+l),'w', linewidth=1)
                ax.set_title('Marker 1 = {}; Marker 2 = {}; Marker 3 = {}; Marker 4 = {}'.format(self.markers[0], self.markers[1], self.markers[2], self.markers[3]))
            plt.gcf().canvas.draw_idle()
        
    def ontype(self, event):
        fig = plt.gcf()
        ax = plt.gca()
        ax.clear()
        if event.key == 'right':
            self.film_dose.array = np.rot90(self.film_dose.array, k=1)
            self.film_dose.plot(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'left':
            self.film_dose.array = np.fliplr(self.film_dose.array)
            self.film_dose.plot(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'up':
            self.film_dose.array = np.flipud(self.film_dose.array)
            self.film_dose.plot(ax=ax)
            fig.canvas.draw_idle()
        
        if event.key == 'enter':
            if len(self.markers) != 4:
                print('')
                print('Please start over...')
                print('{} markers were selected when 4 were expected...'.format(len(self.markers)))
                print('Please double-click on each marker. Press ''enter'' when done')
                self.markers = []
                ax = plt.gca()
                self.film_dose.plot(ax=ax)
                ax.set_title('Top=  ; Right=  ; Bottom=  ; Left=  ')
                fig.canvas.draw_idle()
            else:
                self.fig.canvas.mpl_disconnect(self.cid)
                plt.close(self.fig)
                                
                self.move_iso_center()
                self.remove_rotation()
                if self.ref_dose is not None:
                    self.apply_shifts_ref()
                
                if self.rot:
                    self.film_dose.rotate(self.rot)
                self.tune_registration()
                return
                
    def move_iso_center(self):
        x, y = [m[0] for m in self.markers], [m[1] for m in self.markers]
        t, b = y.index(min(y)), y.index(max(y))
        l, r = x.index(min(x)), x.index(max(x))
        
        # Find intersection -> (x0, y0)
        line1 = ((x[t],y[t]),(x[b],y[b]))
        line2 = ((x[r],y[r]),(x[l],y[l]))
        (x0,y0) = line_intersection(line1, line2)
        
        self.x0 = int(np.around(x0))
        self.y0 = int(np.around(y0))
        
        # Make the (x0, y0) the center of image by padding
        self.film_dose.move_pixel_to_center(x0, y0) 
        
        # Find the corresponding position in the reference image and make it the center
        if self.markers_center is not None:
            self.ref_dose.position = [float(i) for i in self.ref_dose.metadata.ImagePositionPatient]
            self.ref_dose.sizeX = self.ref_dose.metadata.Columns
            self.ref_dose.sizeY = self.ref_dose.metadata.Rows
            self.ref_dose.orientation = self.ref_dose.metadata.SeriesDescription

            if 'Transversal' in self.ref_dose.orientation:
                x_corner = self.ref_dose.position[0]
                y_corner = -1.0 * self.ref_dose.position[1]
                x_marker = self.markers_center[0]
                y_marker = self.markers_center[2]
                x_pos_mm = x_marker - x_corner
                y_pos_mm = y_corner - y_marker
                x0 = int(np.around(x_pos_mm * self.ref_dose.dpmm))
                y0 = int(np.around(y_pos_mm * self.ref_dose.dpmm))

            if 'Sagittal' in self.ref_dose.orientation:
                x_corner = -1.0 * self.ref_dose.position[1]
                y_corner = self.ref_dose.position[2]
                x_marker = self.markers_center[2]
                y_marker = self.markers_center[1]
                x_pos_mm = x_marker - x_corner
                y_pos_mm = y_marker - y_corner
                x0 = self.ref_dose.sizeX + int(np.around(x_pos_mm * self.ref_dose.dpmm))
                y0 = self.ref_dose.sizeY - int(np.around(y_pos_mm * self.ref_dose.dpmm))

            if 'Coronal' in self.ref_dose.orientation:
                x_corner = self.ref_dose.position[0]
                y_corner = self.ref_dose.position[2]
                x_marker = self.markers_center[0]
                y_marker = self.markers_center[1]
                x_pos_mm = x_marker - x_corner
                y_pos_mm = y_marker - y_corner
                x0 = int(np.around(x_pos_mm * self.ref_dose.dpmm))
                y0 = self.ref_dose.sizeY - int(np.around(y_pos_mm * self.ref_dose.dpmm))

            self.ref_dose.move_pixel_to_center(x0, y0)

    def remove_rotation(self):
        x, y = [m[0] for m in self.markers], [m[1] for m in self.markers]
        t, b = y.index(min(y)), y.index(max(y))
        l, r = x.index(min(x)), x.index(max(x))
        
        # Find rotation angle
        angle1 = math.degrees( math.atan( (x[b]-x[t]) / (y[b]-y[t]) ) )
        angle2 = math.degrees( math.atan( (y[l]-y[r]) / (x[r]-x[l]) ) )
        
        # Appy inverse rotation
        angleCorr = -1.0*(angle1+angle2)/2
        print('Applying a rotation of {} degrees'.format(angleCorr))
        self.film_dose.rotate(angleCorr)
        
    def apply_shifts(self):
        shift_x_pixels =  int(round(self.shifts[0] * self.film_dose.dpmm ))
        shift_y_pixels =  int(round(self.shifts[1] * self.film_dose.dpmm ))
        
        if shift_x_pixels > 0:
            self.film_dose.pad(pixels=shift_x_pixels, value=0, edges='left')
        if shift_x_pixels < 0:
            self.film_dose.pad(pixels=abs(shift_x_pixels), value=0, edges='right')
        if shift_y_pixels > 0:
            self.film_dose.pad(pixels=shift_y_pixels, value=0, edges='top')
        if shift_y_pixels < 0:
            self.film_dose.pad(pixels=abs(shift_y_pixels), value=0, edges='bottom')
            
    def apply_shifts_ref(self):
        # Make the isocenter position the center of ref image
        pad_x_pixels =  int(round(self.shifts[0] * self.ref_dose.dpmm )) *2
        pad_y_pixels =  int(round(self.shifts[1] * self.ref_dose.dpmm )) *2
        
        if pad_x_pixels > 0:
            self.ref_dose.pad(pixels=pad_x_pixels, value=0, edges='left')
        if pad_x_pixels < 0:
            self.ref_dose.pad(pixels=abs(pad_x_pixels), value=0, edges='right')
        if pad_y_pixels > 0:
            self.ref_dose.pad(pixels=pad_y_pixels, value=0, edges='top')
        if pad_y_pixels < 0:
            self.ref_dose.pad(pixels=abs(pad_y_pixels), value=0, edges='bottom')
    
    def tune_registration(self): 
        if self.ref_dose is None:
            self.ref_dose = self.film_dose
        film_dose_path = self.film_dose.path
        ref_dose_path = self.ref_dose.path
        
        (self.film_dose, self.ref_dose) = equate_images(self.film_dose, self.ref_dose)
        self.film_dose.path = film_dose_path
        self.ref_dose.path = ref_dose_path
        print('Fine tune registration using keyboard if needed. Arrow keys = move; ctrl+left/right = rotate. Press enter when done.')
        self.fig = plt.figure()
        ax = plt.gca()
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.reg_ontype)
        img_array = self.film_dose.array - self.ref_dose.array
        min_max = [np.percentile(img_array,[1])[0].round(decimals=-1), np.percentile(img_array,[99])[0].round(decimals=-1)] 
        lim = abs(max(min_max, key=abs))
        self.clim = [-1.0*lim, lim]
        self.show_registration(ax=ax)
        
    def show_registration(self, ax=None, cmap='bwr'):
        if ax==None:
                plt.plot()
                ax = plt.gca()
        ax.clear()
        
        if self.register_using_gradient:
            ref_x = spf.sobel(self.ref_dose.as_type(np.float32), 1)
            ref_y = spf.sobel(self.ref_dose.as_type(np.float32), 0)
            ref_grad = np.hypot(ref_x, ref_y)
            film_x = spf.sobel(self.film_dose.as_type(np.float32), 1)
            film_y = spf.sobel(self.film_dose.as_type(np.float32), 0)
            film_grad = np.hypot(film_x, film_y)
            img_array = film_grad - ref_grad
        else:
            img_array = self.film_dose.array - self.ref_dose.array
        img = load(img_array, dpi=self.film_dose.dpi) 
        RMSE =  (sum(sum(img.array**2)) / len(self.film_dose.array[(self.film_dose.array > 0)]))**0.5
        
        #clim = [np.percentile(img_array,[1])[0].round(decimals=-1), np.percentile(img_array,[99])[0].round(decimals=-1)]   
        img.plot(ax=ax, clim=self.clim, cmap=cmap)     
        ax.plot((0, img.shape[1]), (img.center.y, img.center.y),'k--')
        ax.plot((img.center.x, img.center.x), (0, img.shape[0]),'k--')
        ax.set_xlim(0, img.shape[1])
        ax.set_ylim(img.shape[0],0)
        ax.set_title('Fine tune registration. Arrow keys = move; ctrl+left/right = rotate. Press enter when done. RMSE = {}'.format(RMSE))
        
    def reg_ontype(self, event):
        fig = plt.gcf()
        ax = plt.gca()
        if event.key == 'up':
            self.film_dose.roll(direction='y', amount=-1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'down':
            self.film_dose.roll(direction='y', amount=1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'left':
            self.film_dose.roll(direction='x', amount=-1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'right':
            self.film_dose.roll(direction='x', amount=1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'ctrl+right':
            self.film_dose.rotate(-0.1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'ctrl+left':
            self.film_dose.rotate(0.1)
            self.show_registration(ax=ax)
            fig.canvas.draw_idle()
        if event.key == 'enter':
            self.fig.canvas.mpl_disconnect(self.cid)
            plt.close(self.fig)
            self.wait = False
            return
            
    def save_analyzed_image(self, filename,  x=None, y=None, **kwargs):
        """Save the analyzed image to a file. """
        self.show_results(x=x, y=y)
        fig = plt.gcf()
        fig.savefig(filename)
        plt.close(fig)
        
    def save_analyzed_gamma(self, filename, **kwargs):
        """Save the analyzed gamma to a file. """
        self.plot_gamma_stats(**kwargs)
        fig = plt.gcf()
        fig.savefig(filename)
        plt.close(fig)
            
    def publish_pdf(self, filename=None, author=None, unit=None, notes=None, open_file=False, x=None, y=None, **kwargs):
        """Publish a PDF report of the calibration. The report includes basic
        file information, the image and determined ROIs, and the calibration curves

        Parameters
        ----------
        filename : str
            The path and/or filename to save the PDF report as; must end in ".pdf".
        author : str, optional
            The person who analyzed the image.
        unit : str, optional
            The machine unit name or other identifier (e.g. serial number).
        notes : str, list of strings, optional
            If a string, adds it as a line of text in the PDf report.
            If a list of strings, each string item is printed on its own line. Useful for writing multiple sentences.
        """
        if filename is None:
            filename = os.path.join(self.path, 'Report.pdf')
        title='Film Analysis Report'
        canvas = pdf.PylinacCanvas(filename, page_title=title, logo=Path(__file__).parent / 'OMG_Logo.png')
        canvas.add_text(text='Film infos:', location=(1, 25.5), font_size=12)
        text = ['Film dose: {}'.format(os.path.basename(self.film_dose.path)),
                'Film dose factor: {}'.format(self.film_dose_factor),
                'Reference dose: {}'.format(os.path.basename(self.ref_dose.path)),
                'Reference dose factor: {}'.format(self.ref_dose_factor),
                'Film filter kernel: {}'.format(self.film_filt),
                'Gamma threshold: {}'.format(self.threshold),
                'Gamma dose-to-agreement: {}'.format(self.doseTA),
                'Gamma distance-to-agreement: {}'.format(self.distTA),
                'Gamma normalization: {}'.format(self.norm_val)
               ]
        canvas.add_text(text=text, location=(1, 25), font_size=10)
        data = io.BytesIO()
        self.save_analyzed_image(data, x=x, y=y)
        canvas.add_image(image_data=data, location=(0.5, 3), dimensions=(19, 19))
        
        canvas.add_new_page()
        canvas.add_text(text='Analysis infos:', location=(1, 25.5), font_size=12)
        canvas.add_text(text=text, location=(1, 25), font_size=10)
        data = io.BytesIO()
        self.save_analyzed_gamma(data, figsize=(10, 10), **kwargs)
        canvas.add_image(image_data=data, location=(0.5, 2), dimensions=(20, 20))

        canvas.finish()
        if open_file: webbrowser.open(filename)       

    def get_profile_offsets(self):
        self.get_profile_offset(direction='x', side='left')
        self.offset_x_gauche = self.offset
        self.get_profile_offset(direction='x', side='right')
        self.offset_x_droite = self.offset
        self.get_profile_offset(direction='y', side='left')
        self.offset_y_gauche = self.offset
        self.get_profile_offset(direction='y', side='right')
        self.offset_y_droite = self.offset

    def get_profile_offset(self, direction='x', side='left'):
        msg = 'Use left/right keyboard arrows to move profile and fit on ' + side + ' side. Press Enter when done.'
        print(msg)
        self.offset = 0
        self.direction = direction
        self.plot_profile(profile=direction, diff=True, offset=0, title='Fit profiles on ' + side + ' side')
        self.fig = plt.gcf()
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.move_profile_ontype)
        self.wait = True
        while self.wait: plt.pause(5)
                
    def move_profile_ontype(self, event):
        fig = plt.gcf()
        ax = plt.gca()
        
        if event.key == 'left':
            self.offset -= 0.1
            self.plot_profile(ax=ax, profile=self.direction, position=None, title=None, diff=False, offset=self.offset)
            fig.canvas.draw_idle()
            ax.set_title('Shift = ' + str(self.offset) + ' mm')
            
        if event.key == 'right':
            self.offset += 0.1
            self.plot_profile(ax=ax, profile=self.direction, position=None, title=None, diff=False, offset=self.offset)
            fig.canvas.draw_idle()
            ax.set_title('Shift = ' + str(self.offset) + ' mm')
        
        if event.key == 'enter':
            self.fig.canvas.mpl_disconnect(self.cid)
            plt.close(self.fig)
            self.wait = False
            return self.offset

########################### End class DoseAnalysis ############################## 
    
def line_intersection(line1, line2):
    xdiff = (line1[0][0] - line1[1][0], line2[0][0] - line2[1][0])
    ydiff = (line1[0][1] - line1[1][1], line2[0][1] - line2[1][1])

    def det(a, b):
        return a[0] * b[1] - a[1] * b[0]

    div = det(xdiff, ydiff)
    if div == 0:
       raise Exception('lines do not intersect')

    d = (det(*line1), det(*line2))
    x = det(d, xdiff) / div
    y = det(d, ydiff) / div
    return x, y

def save_dose(dose, filename):
    dose.filename = filename
    with open(filename, 'wb') as output:
        pickle.dump(dose, output, pickle.HIGHEST_PROTOCOL)

def load_dose(filename):
    with open(filename, 'rb') as input:
        return pickle.load(input)

def load_analysis(filename):
    print("Loading analysis file {}...".format(filename))
    try:
        file = bz2.open(filename, 'rb')
        analysis = pickle.load(file)
    except:
        file = open(filename, 'rb')
        analysis = pickle.load(file)
    file.close()
    return analysis

def save_analysis(analysis, filename, use_compression=True):
    print("Saving analysis file as {}...".format(filename))
    if use_compression:
        file = bz2.open(filename, 'wb')
    else:
        file = open(filename, 'wb')
    pickle.dump(analysis, file, pickle.HIGHEST_PROTOCOL)
    file.close()