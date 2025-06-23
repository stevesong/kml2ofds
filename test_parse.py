"""
This script is used to KML files to the Open Fibre Data Standard format.
It outputs two geojson files, one for network spans and one for nodes.
Author: Steve Song
Email:  steve@manypossibilities.net
License: GPL 3.0
Date: 13-Nov-2024
Usage: python test_parse.py
"""

import configparser
from datetime import datetime
import uuid
import os
import json
from shapely.geometry import Point, LineString
from shapely.ops import unary_union
import pandas as pd
from pykml import parser
import click
from haversine import haversine, Unit
import sys

def load_config(config_file):
    config = configparser.ConfigParser()
    config.read(config_file)

    # Get all sections from the config file
    sections = config.sections()

    # Initialize an empty dictionary to store the parsed variables
    parsed_config = {}

    # Iterate over each section
    for section in sections:
        # Get all options (variables) within the section
        options = config.options(section)
        
        # Iterate over each option
        for option in options:
            # Get the value of the option
            value = config.get(section, option)
            # Assign the value to a variable with the same name
            parsed_config[option] = value

    return parsed_config

def get_config_value(config, key, default):
    return config.get(key, default)

def load_and_process_config(network_profile):
    network_prof = load_config(network_profile)

    # Default configuration
    default_config = {
        "kml_file_name": None,
        "network_name": "Default Network Name",
        "network_id": str(uuid.uuid4()),
        "network_links": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json",
        "ignore_placemarks": "",
        "input_directory": "input/",
        "output_directory": "output/",
        "output_name_prefix": None
    }
    
    # Update default config with loaded values
    config = {**default_config, **network_prof}
    
    # Validate required fields
    if not config["kml_file_name"]:
        print("Error. Please set kml file name in network profile")
        sys.exit(1)
    
    # Process specific fields
    config["ignore_placemarks"] = config["ignore_placemarks"].split(";") if config["ignore_placemarks"] else []
    
    # Generate date string
    date_string = datetime.now().strftime("%d%b%Y").lower()
    
    # Create directories
    for directory in [config["input_directory"], config["output_directory"]]:
        os.makedirs(directory, exist_ok=True)
    
    # Generate file paths
    kml_fullpath = os.path.join(os.getcwd(), config["input_directory"], config["kml_file_name"])
    network_filename_normalised = config["kml_file_name"].replace(" ", "_").upper()
    
    if not config["output_name_prefix"]:
        config["output_name_prefix"] = network_filename_normalised[3:]
    
    # Generate output file names
    output_files = {
        "nodes": f"{config['output_directory']}{config['output_name_prefix']}_ofds-nodes_{date_string}.geojson",
        "spans": f"{config['output_directory']}{config['output_name_prefix']}_ofds-spans_{date_string}.geojson",
        "json": f"{config['output_directory']}{config['output_name_prefix']}_ofds-json_{date_string}.json"
    }
    
    return config, kml_fullpath, output_files

def extract_linestrings(root, namespace="{http://www.opengis.net/kml/2.2}"):
    """
    Extracts all LineString coordinates from a KML root element, handling any level of nesting.
    """
    lines = []
    # XPath to find all Placemark elements containing LineString within any structure
    placemarks = root.findall(f".//{namespace}Placemark")
    
    for placemark in placemarks:
        # Check if the Placemark has a LineString within it (even if nested in MultiGeometry)
        linestrings = placemark.findall(f".//{namespace}LineString")
        for linestring in linestrings:
            coordinates = linestring.find(f"{namespace}coordinates")
            if coordinates is not None:
                lines.append(coordinates.text.strip())
    
    return lines

def make_node(name, network_id, network_name, x, y):
    node_id = str(uuid.uuid4())
    geojson_node = {
        "type": "Feature",
        "properties": {
            "name": name,
            "id": node_id,
            "network": {
                "id": network_id,
                "name": network_name,
                "links": [
                    {
                        "rel": "describedby",
                        "href": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json",
                    }
                ],
            },
            "featureType": "node",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [x, y],
        },
    }
    return geojson_node

def make_line(name, network_id, network_name, coordinates):
    """
    Creates a GeoJSON feature for a polyline (LineString).
    """
    span_id = str(uuid.uuid4())
    geojson_line = {
        "type": "Feature",
        "properties": {
            "name": name,
            "id": span_id,
            "network": {
                "id": network_id,
                "name": network_name,
                "links": [
                    {
                        "rel": "describedby",
                        "href": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json",
                    }
                ],
            },
            "featureType": "line",
        },
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
    }
    return geojson_line

def extract_points_to_geojson(root, network_id, network_name, namespace="{http://www.opengis.net/kml/2.2}"):
    """
    Extracts all Points from the KML root element, converts them to GeoJSON features using make_node,
    and returns a list of GeoJSON feature nodes.
    """
    geojson_nodes = []
    # XPath to find all Placemark elements containing Point within any structure
    placemarks = root.findall(f".//{namespace}Placemark")
    
    for placemark in placemarks:
        # Retrieve the name of the placemark, if it exists
        name_element = placemark.find(f"{namespace}name")
        name = name_element.text.strip() if name_element is not None else "Unnamed"

        # Check if the Placemark has a Point within it
        points_found = placemark.findall(f".//{namespace}Point")
        for point in points_found:
            coordinates = point.find(f"{namespace}coordinates")
            if coordinates is not None:
                # Extract longitude and latitude from the coordinates text
                lon, lat, *_ = map(float, coordinates.text.strip().split(','))
                # Create GeoJSON node using make_node function
                geojson_node = make_node(name, network_id, network_name, lon, lat)
                geojson_nodes.append(geojson_node)
    
    # Create a complete GeoJSON object
    geojson_nodes_object = {
        "type": "FeatureCollection",
        "features": geojson_nodes
    }
    
    return geojson_nodes_object

def extract_lines_to_geojson(root, network_id, network_name, namespace="{http://www.opengis.net/kml/2.2}"):
    """
    Extracts all LineString elements from the KML root, converts them to GeoJSON features using make_line,
    and returns a list of GeoJSON line features.
    """
    geojson_lines = []
    # XPath to find all Placemark elements containing LineString within any structure
    placemarks = root.findall(f".//{namespace}Placemark")
    
    for placemark in placemarks:
        # Retrieve the name of the placemark, if it exists
        name_element = placemark.find(f"{namespace}name")
        name = name_element.text.strip() if name_element is not None else "Unnamed"

        # Check if the Placemark has a LineString within it
        linestrings = placemark.findall(f".//{namespace}LineString")
        for linestring in linestrings:
            coordinates_element = linestring.find(f"{namespace}coordinates")
            if coordinates_element is not None:
                # Parse the coordinates as a list of [longitude, latitude] pairs
                coordinates = []
                coord_pairs = coordinates_element.text.strip().split()
                for pair in coord_pairs:
                    lon, lat, *_ = map(float, pair.split(','))
                    coordinates.append([lon, lat])
                
                # Create GeoJSON line feature using make_line function
                geojson_line = make_line(name, network_id, network_name, coordinates)
                geojson_lines.append(geojson_line)
    
    # Create a complete GeoJSON object
    geojson_lines_object = {
        "type": "FeatureCollection",
        "features": geojson_lines
    }
    
    return geojson_lines_object

def remove_overlapping_segments(lines_geojson):
    """
    Removes overlapping segments in polylines within a GeoJSON structure.
    """
    # Convert each line in the GeoJSON to a Shapely LineString
    lines = [LineString(feature['geometry']['coordinates']) for feature in lines_geojson['features']]
    
    # Create an empty list to store the updated, non-overlapping LineStrings
    non_overlapping_lines = []
    
    # Process each line and remove overlapping segments
    for i, line in enumerate(lines):
        # Remove overlaps with all other lines by subtracting their union
        other_lines = lines[:i] + lines[i + 1:]
        other_lines_union = unary_union(other_lines)
        
        # Subtract the union of other lines from the current line to get non-overlapping parts
        difference = line.difference(other_lines_union)
        
        # If the result is a single LineString or MultiLineString, add each part individually
        if difference.is_empty:
            print(f"Line {i} is fully overlapped and removed.")
        elif difference.geom_type == 'LineString':
            non_overlapping_lines.append(difference)
        elif difference.geom_type == 'MultiLineString':
            non_overlapping_lines.extend(difference.geoms)

    # Convert the non-overlapping LineStrings back to GeoJSON format
    non_overlapping_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": list(line.coords)
                },
                "properties": {}
            }
            for line in non_overlapping_lines
        ]
    }
    
    return non_overlapping_geojson

def adjust_line_endpoints(lines_geojson, points_geojson, proximity_threshold=400):
    """
    Adjusts the start and end vertices of each line to match any nearby point if
    it is within the specified proximity threshold.
    """
    # Ensure points_geojson has a 'features' key
    if 'features' not in points_geojson:
        raise ValueError("Invalid GeoJSON format: 'features' key not found in points_geojson.")
    
    # Extract all points' coordinates
    points = [(feature['geometry']['coordinates'], feature['properties'].get('name', 'Unnamed'))
              for feature in points_geojson['features']]
    
    for line in lines_geojson['features']:
        if line['geometry']['type'] == 'LineString' and len(line['geometry']['coordinates']) >= 2:
            start_vertex = line['geometry']['coordinates'][0]
            end_vertex = line['geometry']['coordinates'][-1]
            
            # Check start and end vertices against each point
            for vertex in [start_vertex, end_vertex]:
                for point_coords, point_name in points:
                    # Calculate distance
                    distance = haversine(vertex, point_coords, unit=Unit.METERS)
                    
                    # If the vertex is within the proximity threshold, snap it to the point
                    if distance <= proximity_threshold:
                        print(f"Snapping line end point {point_name} to point {point_coords} (distance: {distance:.2f} m)")
                        vertex[:] = point_coords  # Update vertex to match point's coordinates
                        break  # Stop searching once a match is found within the threshold

    return lines_geojson

def adjust_points_to_nearby_polylines(points_geojson, lines_geojson, proximity_threshold=400):
    """
    Adjust points so that those close to but not on a polyline are moved directly onto the polyline.
    """
    for point in points_geojson['features']:
        if point['geometry']['type'] == 'Point':
            point_coords = point['geometry']['coordinates']
            point_shapely = Point(point_coords)
            closest_location = None
            min_distance = float('inf')
            
            # Iterate through each polyline to find the nearest segment
            for line in lines_geojson['features']:
                if line['geometry']['type'] == 'LineString':
                    line_coords = line['geometry']['coordinates']
                    line_shapely = LineString(line_coords)
                    
                    # Skip snapping if the point is already on the line
                    if point_shapely.within(line_shapely):
                        closest_location = None
                        print(f"Point {point_coords} is already on the line.")
                        break

                    # Calculate the distance to the line
                    distance = point_shapely.distance(line_shapely)
                    
                    # If within proximity threshold, consider snapping the point
                    if distance < min_distance and distance <= proximity_threshold:
                        min_distance = distance
                        closest_location = line_shapely.interpolate(line_shapely.project(point_shapely))
            
            # Update point to the closest location on line if within the threshold
            if closest_location:
                snapped_coords = list(closest_location.coords)[0]
                print(f"Adjusting point {point_coords} to line at {snapped_coords} (distance: {min_distance:.2f} m)")
                point['geometry']['coordinates'] = snapped_coords

    return points_geojson

@click.command(help="Convert KML files to the Open Fibre Data Standard format.")
@click.option('--network-profile', help='Load variables from network profile.')
def main(network_profile):
    config, kml_fullpath, output_files = load_and_process_config(network_profile)
    
    # Now you can use config["network_name"], config["network_id"], etc.
    # And output_files["nodes"], output_files["spans"], etc.
    with open(kml_fullpath, 'r') as f:
        root = parser.parse(f).getroot()
    geojson_nodes = extract_points_to_geojson(root, config["network_id"], config["network_name"], namespace="{http://www.opengis.net/kml/2.2}")
    geojson_spans = extract_lines_to_geojson(root, config["network_id"], config["network_name"], namespace="{http://www.opengis.net/kml/2.2}")
    print(f"Found {len(geojson_nodes['features'])} nodes")
    print(f"Found {len(geojson_spans['features'])} spans")
    geojson_spans = remove_overlapping_segments(geojson_spans)
    # geojson_spans = adjust_line_endpoints(geojson_spans, geojson_nodes)
    # geojson_nodes = adjust_points_to_nearby_polylines(geojson_nodes, geojson_spans)
    # Writing nodes to file
    with open(output_files["nodes"], 'w', encoding='utf-8') as f:
        json.dump(geojson_nodes, f, indent=2, ensure_ascii=False)

    # Writing spans to file
    with open(output_files["spans"], 'w', encoding='utf-8') as f:
        json.dump(geojson_spans, f, indent=2, ensure_ascii=False)
    
    print(f"Completed processing of {kml_fullpath}")

# main
if __name__ == "__main__":
    main()