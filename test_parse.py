from pykml import parser
import os
import inquirer 
import json
import numpy as np
from shapely.geometry import MultiPolygon, Point
from shapely.ops import split, nearest_points
import geopandas as gpd
import pandas as pd
import uuid
import random
import matplotlib
matplotlib.use('Qt5Agg')  # Choose an appropriate backend
import matplotlib.pyplot as plt

def list_kml_files(directory):
    # List all .kml files in the given directory.
    kml_files = [f for f in os.listdir(directory) if f.endswith('.kml')]
    return kml_files

def select_file(files):
    # Allow the user to select a file from the list.
    questions = [
        inquirer.List('file',
                      message="Select a file",
                      choices=files,
                      ),
    ]
    answers = inquirer.prompt(questions)
    return answers['file']

def process_document(document):
    # Process the current Document
    print(f"Processing Document: {document.name.text}")

    # Process Folders within the Document
    for folder in document.iter('{http://www.opengis.net/kml/2.2}Folder'):
        print(f" Folder: {folder.name.text}")
        # Process Folders or Placemarks within this Folder
        # You can add more processing logic here

def process_kml(filename):
    with open(filename) as f:
        kml_doc = parser.parse(f).getroot()
    
    # Start processing from the root Document
    for document in kml_doc.iter('{http://www.opengis.net/kml/2.2}Document'):
        process_document(document)

def main():
    
    # Prompt the user for a directory, defaulting to the "input" subdirectory if none is provided.
    directory = input("Enter the directory path for kml files \n(leave blank to use the 'input' subdirectory): ").strip()
    if not directory:
        directory = os.path.join(os.getcwd(), 'input')
    
    kml_files = list_kml_files(directory)

    if not kml_files:
        print("No .kml files found in the directory.")
        exit()
    
    kml_file = select_file(kml_files)
    kml_fullpath = os.path.join(directory, kml_file)
    # set file names
    base_name = os.path.splitext(os.path.basename(kml_fullpath))[0]

    # Allow the user to select a folder level from the KML file
    process_kml(kml_fullpath)

# main
if __name__ == "__main__":
    main()
    