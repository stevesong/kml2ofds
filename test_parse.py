from pykml import parser
import os
import inquirer
import json
import numpy as np
from shapely.geometry import MultiPolygon, Point, LineString
from shapely.ops import split, nearest_points
import geopandas as gpd
import pandas as pd
import uuid
import random

# import matplotlib
# matplotlib.use('Qt5Agg')  # Choose an appropriate backend
# import matplotlib.pyplot as plt


def list_kml_files(directory):
    # List all .kml files in the given directory.
    kml_files = [f for f in os.listdir(directory) if f.endswith(".kml")]
    return kml_files


def select_file(files):
    # Allow the user to select a file from the list.
    questions = [
        inquirer.List(
            "file",
            message="Select a file",
            choices=files,
        ),
    ]
    answers = inquirer.prompt(questions)
    return answers["file"]


def process_kml(filename):
    with open(filename) as f:
        kml_doc = parser.parse(f).getroot()
    geojson_nodes = []
    geojson_spans = []
    # Start processing from the root Document
    # First look for multiple Documents within the KML file.
    for document in kml_doc.iter("{http://www.opengis.net/kml/2.2}Document"):
        nodes, spans = process_document(document)
        geojson_nodes.extend(nodes)
        geojson_spans.extend(spans)

    gdf_nodes = gpd.GeoDataFrame.from_features(geojson_nodes)
    gdf_spans = gpd.GeoDataFrame.from_features(geojson_spans)

    snapped_nodes = gdf_nodes.geometry.apply(
        lambda point: snap_to_line(point, gdf_spans)
    )
    print("Total number of snapped points:", snapped_nodes.size)

    # Create a new GeoDataFrame with the snapped points and geojson features
    gdf_ofds_nodes = gpd.GeoDataFrame(
        gdf_nodes.drop(columns="geometry"), geometry=snapped_nodes
    )

    # Save GeoJSON objects to a file
    with open("output/nodes.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_nodes}, f)
    with open("output/spans.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_spans}, f)

    # Save snapped nodes to geojson file
    gdf_ofds_nodes.to_file("output/nodes_ofds.geojson", driver="GeoJSON")
    return gdf_ofds_nodes, gdf_spans


def process_document(document):
    """Process a KML Document and return a list of GeoJSON nodes and spans.

    Args:
        document (ElementTree.Element): The KML Document to process.

    Returns:
        tuple: A tuple containing two lists of GeoJSON objects. The first list contains GeoJSON nodes (Points),
        and the second list contains GeoJSON spans (LineStrings).
    """
    geojson_nodes = []
    geojson_spans = []

    network_name = "My Fibre Network"
    network_id = str(uuid.uuid4())

    # Process Folders within the Document
    for folder in document.iter("{http://www.opengis.net/kml/2.2}Folder"):
        print(f" Folder: {folder.name.text}")
        # Process Placemarks within this Folder
        for placemark in folder.iter("{http://www.opengis.net/kml/2.2}Placemark"):

            # name = placemark.find('{http://www.opengis.net/kml/2.2}name').text
            name_element = placemark.find("{http://www.opengis.net/kml/2.2}name")
            name = name_element.text if name_element is not None else "Default Name"

            # Process points
            geometry = placemark.find("{http://www.opengis.net/kml/2.2}Point")
            if geometry is not None:
                # Convert KML Point to Shapely Point
                shapely_point = Point(
                    float(
                        geometry.find(
                            "{http://www.opengis.net/kml/2.2}coordinates"
                        ).text.split(",")[0]
                    ),
                    float(
                        geometry.find(
                            "{http://www.opengis.net/kml/2.2}coordinates"
                        ).text.split(",")[1]
                    ),
                )
                # Convert Shapely Point to GeoJSON
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
                        "coordinates": [shapely_point.x, shapely_point.y],
                    },
                }
                # Add the GeoJSON object to the list
                geojson_nodes.append(geojson_node)

            # Process Polylines
            polyline = placemark.find("{http://www.opengis.net/kml/2.2}LineString")
            if polyline is not None:
                coordinates_text = polyline.find(
                    "{http://www.opengis.net/kml/2.2}coordinates"
                ).text
                coordinates = [
                    tuple(map(float, coord.split(",")))
                    for coord in coordinates_text.split()
                ]
                # Convert to Shapely LineString
                shapely_line = LineString(coordinates)
                # Convert Shapely LineString to GeoJSON
                geojson_span = {
                    "type": "Feature",
                    "properties": {
                        "id": "",
                        "name": name,
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
                        "featureType": "span",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": list(shapely_line.coords)
                    },
                }
                # Add the GeoJSON object to the list
                geojson_spans.append(geojson_span)

    # Return the list of GeoJSON objects
    return geojson_nodes, geojson_spans


def snap_to_line(point, lines):
    """Find the nearest line to a given point and find the nearest point on that line to the given point.

    Args:
        point (Point): The point to snap to the nearest line.
        lines (LineString): The collection of lines to search for the nearest line.

    Returns:
        Point: The nearest point on the nearest line to the given point.
    """
    nearest_line = None
    min_distance = float("inf")
    nearest_point_on_line = None

    # Iterate over all lines to find the nearest one
    for line in lines.geometry:
        # Use nearest_points to get the nearest point on the line to our point
        point_on_line = nearest_points(point, line)[1]
        distance = point.distance(point_on_line)

        if distance < min_distance:
            min_distance = distance
            nearest_line = line
            nearest_point_on_line = point_on_line

    return nearest_point_on_line

def break_spans_at_node_points(gdf_nodes, gdf_spans, network_name, network_id, network_links):
    """
    Breaks the spans into segments at each node intersection.

    Args:
        gdf_nodes (GeoDataFrame): GeoDataFrame containing the node points.
        gdf_spans (GeoDataFrame): GeoDataFrame containing the spans.
        network_name (str): Name of the network.
        network_id (str): ID of the network.
        network_links (str): Links of the network.

    Returns:
        GeoDataFrame: GeoDataFrame containing the split linestrings.
    """
    split_lines = []
    featureType = "span"

    # Iterate over the spans and find the nodes that intersect each span
    # breaking the spans into segments at each node intersection
    for line_row in gdf_spans.iterrows():
        span_name = line_row['name']
        buffered_points = []
        point_names = []
        
        # Create a buffer around each node point
        for point_row in gdf_nodes.iterrows():
            point = point_row.geometry
            point_name = point_row['name']
            buffered_point = point.buffer(1e-9)
            buffered_points.append(buffered_point)
            
            # Check if the line intersects the buffered point and add the point name to the point_names list
            if line_row.geometry.intersects(buffered_point):
                point_names.append(point_name)  # Capture the name of the intersecting point
        
        buffered_area = MultiPolygon(buffered_points)
        
        if line_row.geometry.intersects(buffered_area):
            split_line = split(line_row.geometry, buffered_area)
            for segment in split_line.geoms:
                # Create a unique identifier for each line segment
                segment_uuid = str(uuid.uuid4())
                # Include both polyline and point names with the geometry
                split_lines.append((segment_uuid, segment, span_name, ", ".join(point_names)))
        else:
            # Generate a UUID for the original line if no intersection
            segment_uuid = str(uuid.uuid4())
            split_lines.append((segment_uuid, line_row.geometry, span_name, ""))

    # print("Number of segments found:", len(split_lines))        

    # Create a new GeoDataFrame from the split linestrings
    gdf_basic_spans = gpd.GeoDataFrame(split_lines, columns=['id', 'geometry', 'name', 'featureType', 'point_names'])

    # Add network metadata to the split spans GeoDataFrame
    gfd_basic_spans = gdf_basic_spans.apply(lambda row: update_network_field(row, network_name, network_id, network_links), axis=1)
    
    # Ensure that each segment has a start and end node
    # If not, add the missing nodes to the ofds_points_gdf
    tolerance = 1e-3 # Adjustable
    new_nodes = [] # Store new nodes to be appended to the ofds_points_gdf
    for idx, row in basic_spans_gdf.iterrows():
        start_point = row.geometry.coords[0]
        end_point = row.geometry.coords[-1]
        
        # Create buffers around the start and end points
        start_buffer = Point(start_point).buffer(tolerance)
        end_buffer = Point(end_point).buffer(tolerance)
        
        # Check if start and end points exist in ofds_points_gdf within the buffer
        start_exists = ofds_points_gdf.geometry.intersects(start_buffer).any()
        end_exists = ofds_points_gdf.geometry.intersects(end_buffer).any()
        
        # Add points if they don't exist
        if not start_exists:
            new_node = append_node(start_point, network_id, network_name, network_links)
            new_nodes.append(new_node)
        if not end_exists:
            new_node = append_node(end_point, network_id, network_name, network_links)
            new_nodes.append(new_node)
    
    print(len(new_nodes), " new nodes added where spans did not have a node at a start or end point")
    
     # Convert the list of new nodes into a GeoDataFrame
    if new_nodes:
        new_nodes_gdf = gpd.GeoDataFrame.from_features(new_nodes, crs=ofds_points_gdf.crs)
        ofds_points_gdf = pd.concat([ofds_points_gdf, new_nodes_gdf], ignore_index=True)
    ofds_points_gdf.to_file(ofds_points_geojson_file , driver='GeoJSON')

    # Add the OFDS node metadata to the split span lines
    ofds_spans_gdf = add_nodes_to_spans(basic_spans_gdf, ofds_points_gdf)

    # Apply conversion to 'start' and 'end' columns in the ofds_spans_gdf DataFrame
    ofds_spans_gdf['start'] = ofds_spans_gdf['start'].apply(lambda x: json.dumps(convert_to_serializable(x)) if x is not None else None)
    ofds_spans_gdf['end'] = ofds_spans_gdf['end'].apply(lambda x: json.dumps(convert_to_serializable(x)) if x is not None else None)
    
    # Save the split lines to a new GeoJSON file
    ofds_spans_gdf.to_file(ofds_polylines_geojson_file, driver='GeoJSON')
    
# Function to update the 'network' field with 'links'
def update_network_field(row, network_name, network_id, network_links):
    # Check if 'network' key exists in the row's dictionary
    if 'network' not in row:
        # If 'network' does not exist, create it as a dictionary
        row['network'] = {}
    
    # Update 'id' and 'name' in the 'network' dictionary
    row['network']['id'] = network_id
    row['network']['name'] = network_name
    row['network']['links'] = network_links
    
    return row
    
    

def main():
    
    # set network name,id, and links  (TODO: remove this and use a config file)
    network_name = "My Fibre Network"
    network_id = str(uuid.uuid4())
    network_links = [
        {
            "rel": "describedby",
            "href": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json"
        }
    ]
    # Prompt the user for a directory, defaulting to the "input" subdirectory if none is provided.
    directory = input(
        "Enter the directory path for kml files \n(leave blank to use the 'input' subdirectory): "
    ).strip()
    if not directory:
        directory = os.path.join(os.getcwd(), "input")

    kml_files = list_kml_files(directory)

    if not kml_files:
        print("No .kml files found in the directory.")
        exit()

    kml_file = select_file(kml_files)
    kml_fullpath = os.path.join(directory, kml_file)
    # set file names
    base_name = os.path.splitext(os.path.basename(kml_fullpath))[0]

    gdf_ofds_nodes, gdf_spans = process_kml(kml_fullpath)
    break_spans_at_node_points(gdf_ofds_nodes, gdf_spans, network_name, network_id, network_links)


# main
if __name__ == "__main__":
    main()
