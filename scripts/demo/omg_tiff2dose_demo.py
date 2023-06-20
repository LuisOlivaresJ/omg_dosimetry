# -*- coding: utf-8 -*-
"""
This script is used to demonstrate an example using of the omg_dosimetry tiff2dose module.
You can make a copy and adapt it according to your needs.
    
Written by 
Jean-François Cabana
jean-francois.cabana.cisssca@ssss.gouv.qc.ca

2023-03-01
"""

#%% Import libraries
from omg_dosimetry import tiff2dose
import os

#%% Define general information
info = dict(author = 'Demo Physicist',
            unit = 'Demo Linac',
            film_lot = 'XD_1',
            scanner_id = 'Epson 72000XL',
            date_exposed = '2023-01-24 16h',
            date_scanned = '2023-01-25 16h',
            wait_time = '24 heures',
            notes = 'Transmission mode, @300ppp and 16 bits/channel'
           )

path = os.path.join(os.path.dirname(__file__), "files", "tiff2dose")   # Root folder
path_scan = os.path.join(path, "scan",'A1A_Multi_6cm_001.tif')         # 0'
outname = "Demo_dose"                                                  # Nom de fichier à produire (tif et pdf)

#%% Définir les paramètres de conversion en dose
lut_file = os.path.join(os.path.dirname(__file__), "files", "calibration","Demo_calib.pkl")   # Chemin vers le fichier LUT à utiliser
fit_type = 'rational'                                                                         # Type de fonction à utiliser pour le fit de la courbe de calibration. 'rational' ou 'spline'
clip = 500                                                                                    # Valeur maximale [cGy] à laquelle la dose doit être limitée. Utile pour éviter des valeurs de dose extrêmes par exemple sur les marques faites sur le film.

#%% Effectuer la conversion en dose
gaf1 = tiff2dose.Gaf(path=path_scan, lut_file=lut_file, fit_type=fit_type, info=info, clip = clip)

#%% Sauvegarder la dose et produire le rapport PDF
filename_tif = os.path.join(path, outname+'.tif')
gaf1.dose_opt.save(filename_tif)                    # On sauvegarde la "dose_opt". D'autres options sont disponibles également.

filename_pdf = os.path.join(path, outname+'.pdf')
gaf1.publish_pdf(filename_pdf, open_file=True)      # Publication du rapport PDF