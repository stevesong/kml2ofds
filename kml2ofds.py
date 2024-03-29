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

# See how deep the folder hierarchy goes
def get_folder_levels(folder, level=1):
    levels = []
    if hasattr(folder, 'name'):
        levels.append((f"{'    ' * level}Folder Level {level}: {folder.name}", level))

    for child_folder in getattr(folder, 'Folder', []):
        levels.extend(get_folder_levels(child_folder, level + 1))
    return levels

#  Allow user to select a folder level depth to process
def select_level(levels):
    questions = [
        inquirer.List('level',
                      message="Please choose what folder to process into OFDS.  Select a folder level:",
                      choices=[level[0] for level in levels],
                      carousel=True)
    ]
    answers = inquirer.prompt(questions)
    if answers:
        selected_option = answers['level']
        # Extract the level number from the selected option
        for level in levels:
            if level[0] == selected_option:
                return int(level[1])
    else:
        print("No selection made. Exiting.")
        exit()

def choose_folder_level(filename):
    with open(filename) as f:
        root = parser.parse(f).getroot()
        levels = []
        for folder in root.Document.Folder:
            levels.extend(get_folder_levels(folder))

    user_choice = select_level(levels)
    print(f"\nSelected folder level: {user_choice}. Processing...")

    return user_choice

def parse_coordinates(coordinates_text, geom_type):
    if geom_type == "Point":
        # Split the coordinates text by comma and convert the first two elements to float
        x, y, *_ = map(float, coordinates_text.split(","))
        return [x, y]
    elif geom_type == "LineString":
        # return [tuple(map(float, point.split(","))) for point in coordinates_text.split()]
        return [tuple(map(float, point.split(",")[:2])) for point in coordinates_text.split()]

def create_geojson_feature(geometry_type, name, coordinates):
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "properties": {"name": name}
    }

def process_placemarks(placemarks, level, selected_level, points, polylines):
    for placemark in placemarks:
        name = placemark.name
        print(f"Level {level} Placemark: {name}")
        if level >= selected_level:
            if hasattr(placemark, "Point"):
                coordinates = parse_coordinates(placemark.Point.coordinates.text, "Point")
                points.append(create_geojson_feature("Point", name, coordinates))
            elif hasattr(placemark, "LineString"):
                coordinates = parse_coordinates(placemark.LineString.coordinates.text, "LineString")
                polylines.append(create_geojson_feature("LineString", name, coordinates))

def process_folders(filename, selected_level, points_geojson_file, polylines_geojson_file):
    points, polylines = [], []

    with open(filename) as f:
        root = parser.parse(f).getroot()
        for folder in root.Document.Folder:
            process_folders_recursive(folder, 1, selected_level, points, polylines)

    # Write to GeoJSON files
    for geojson_file, features in [(points_geojson_file, points), (polylines_geojson_file, polylines)]:
        with open(geojson_file, "w") as output:
            json.dump({"type": "FeatureCollection", "features": features}, output, indent=2, default=str)

    # return points, polylines

def process_folders_recursive(folder, level, selected_level, points, polylines):
    if hasattr(folder, "name"):
        if level >= selected_level:
            print(f"Processing Level {level}: {folder.name}")
            process_placemarks(getattr(folder, 'Placemark', []), level, selected_level, points, polylines)

        for child_folder in getattr(folder, 'Folder', []):
            process_folders_recursive(child_folder, level + 1, selected_level, points, polylines)

# Function to find the nearest line to each point and find the nearest point on that line to the point
def snap_to_line(point, lines):
    nearest_line = None
    min_distance = float('inf')
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

def snap_all_points(points, lines, network_name, network_id, network_links, ofds_points_geojson_file):
    # Ensure CRS match, if not, reproject
    if points.crs != lines.crs:
        points = points.to_crs(lines.crs)
        
    # Apply the snap_to_line function to each point in the points GeoDataFrame
    snapped_points = points.geometry.apply(lambda point: snap_to_line(point, lines))
    print("Total number of snapped points:", snapped_points.size)
    
    # Create a new GeoDataFrame with the snapped points
    snapped_points_gdf = gpd.GeoDataFrame(points.drop(columns='geometry'), geometry=snapped_points, crs=points.crs)

    # Add metadata to the snapped points GeoDataFrame
    snapped_points_gdf['id'] = [str(uuid.uuid4()) for _ in range(len(points))]
    # Add network metadata to the split spans GeoDataFrame
    snapped_points_gdf = snapped_points_gdf.apply(lambda row: update_network_field(row, network_name, network_id, network_links), axis=1)
    snapped_points_gdf['featureType'] = 'node'
    
    # Save the snapped points dataframe to a new GeoJSON file
    snapped_points_gdf.to_file(ofds_points_geojson_file , driver='GeoJSON')
    return snapped_points_gdf
    
def load_geojson(filename):
    with open(filename, 'r') as file:
        return json.load(file)

def save_geojson(data, filename):
    with open(filename, 'w') as file:
        json.dump(data, file)

def find_end_point(polyline_endpoint, gdf_points, tolerance=1e-3):
    point_geom = Point(polyline_endpoint)
    # Create a buffer around the point with the specified tolerance
    buffered_point = point_geom.buffer(tolerance)
    # Filter points that are within the buffer
    matched_points = gdf_points[gdf_points.geometry.within(buffered_point)]
    if len(matched_points) > 1:
        print(f"{len(matched_points)} points found within the buffer")
    if not matched_points.empty:
        # Calculate distances from the endpoint to each matched point
        distances = matched_points.geometry.apply(lambda geom: point_geom.distance(geom))
        # Find the index of the point with the minimum distance
        closest_point_index = distances.idxmin()
        # Return the closest matched point
        return matched_points.loc[closest_point_index]
    else:
        return None # Return None if no match is found

def add_nodes_to_spans(gdf_polylines, gdf_points):
    start_points = []
    end_points = []

    for _, polyline in gdf_polylines.iterrows():
        start_point_geom = polyline.geometry.coords[0]
        end_point_geom = polyline.geometry.coords[-1]
        
        # Find the point with the same coordinates as the start and end points
        matching_start_point = find_end_point(start_point_geom, gdf_points)
        matching_end_point = find_end_point(end_point_geom, gdf_points)
        
        if matching_start_point is not None:
            start_points_info = {
                "id": matching_start_point['id'],
                "name": matching_start_point['name'],
                "location": {
                    "type": "Point",
                    "coordinates": [matching_start_point.geometry.x, matching_start_point.geometry.y]
                }
            }
        else:
            start_points_info = None

        if matching_end_point is not None:
            end_points_info = {
                "id": matching_end_point['id'],
                "name": matching_end_point['name'],
                "status": matching_end_point.get('status', 'unknown'),
                "location": {
                    "type": "Point",
                    "coordinates": [matching_end_point.geometry.x, matching_end_point.geometry.y]
                }
            }
        else:
            end_points_info = None

        # Append the matching points information to the lists
        start_points.append(start_points_info)
        end_points.append(end_points_info)

    # Add the start and end points information to the polylines DataFrame
    gdf_polylines['start'] = start_points
    gdf_polylines['end'] = end_points

    return gdf_polylines

# Function to convert a dictionary to JSON, ensuring all numeric values are Python native types
def convert_to_serializable(obj):
    if isinstance(obj, dict):
        return {key: convert_to_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(element) for element in obj]
    elif isinstance(obj, (np.int64, np.int32, np.int16)):
        return int(obj)
    elif isinstance(obj, (np.float64, np.float32, np.float16)):
        return float(obj)
    else:
        return obj

def break_polylines_at_nodepoints(ofds_points_gdf, lines_gdf, ofds_points_geojson_file, ofds_polylines_geojson_file, network_name, network_id, network_links):
    split_lines = []
    
    # Iterate over the lines and find the snapped points that intersect each line
    # breaking the lines into segments at the intersection points
    for idx, line_row in lines_gdf.iterrows():
        polyline_name = line_row['name']
        feature_type = "span"
        buffered_points = []
        point_names = []
        
        for point_idx, point_row in ofds_points_gdf.iterrows():
            point = point_row.geometry
            point_name = point_row['name']
            buffered_point = point.buffer(1e-9)
            buffered_points.append(buffered_point)
            
            if line_row.geometry.intersects(buffered_point):
                point_names.append(point_name)  # Capture the name of the intersecting point
        
        buffered_area = MultiPolygon(buffered_points)
        
        if line_row.geometry.intersects(buffered_area):
            split_line = split(line_row.geometry, buffered_area)
            for segment in split_line.geoms:
                # Create a unique identifier for each line segment
                segment_uuid = str(uuid.uuid4())
                # Include both polyline and point names with the geometry
                split_lines.append((segment_uuid, segment, polyline_name, feature_type, ", ".join(point_names)))
        else:
            # Generate a UUID for the original line if no intersection
            segment_uuid = str(uuid.uuid4())
            split_lines.append((segment_uuid, line_row.geometry, polyline_name, feature_type, ""))

    # print("Number of segments found:", len(split_lines))        

    # Create a new GeoDataFrame from the split linestrings
    basic_spans_gdf = gpd.GeoDataFrame(split_lines, columns=['id', 'geometry', 'name', 'featureType', 'point_names'], crs=lines_gdf.crs)

    # Add network metadata to the split spans GeoDataFrame
    basic_spans_gdf = basic_spans_gdf.apply(lambda row: update_network_field(row, network_name, network_id, network_links), axis=1)
    
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


def append_node(new_node_coords,network_id, network_name, network_links):
    # Returns a GeoJSON feature dictionary representing the new node 
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": new_node_coords
        },
        "properties": {
            "id": str(uuid.uuid4()), # Generate a new UUID for the id
            "name": "Auto generated missing node", # You might want to generate a more descriptive name
            "network": {
                "id": network_id,
                "name": network_name,
                "links": network_links
            },
            "featureType": "node"
        }
    }


# Function to generate a random color
def random_color():
    return "#" + ''.join([random.choice('0123456789ABCDEF') for j in range(6)])

def plot_results(points_gdf, linestrings_gdf):
    # ##  Plot the snapped points and split lines
    fig, ax = plt.subplots()
    # Iterate through each row and plot with a random color
    for _, row in linestrings_gdf.iterrows():
        random_color = "#" + ''.join([random.choice('0123456789ABCDEF') for j in range(6)])
        ax.plot(*row.geometry.xy, color=random_color)

    # Plot the points
    points_gdf.plot(ax=ax, color='red', markersize=5)
    plt.show()

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
    
    # set network name,id, and links  (TODO: remove this and use a config file or prompt user for input)
    network_name = "My Fibre Network"
    network_id = str(uuid.uuid4())
    network_links = [
        {
            "rel": "describedby",
            "href": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json"
        }
    ]

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
    points_geojson_file = "output/" + base_name + "-points.geojson"
    ofds_points_geojson_file = "output/" + base_name + "-ofds-points.geojson"
    polylines_geojson_file = "output/" + base_name + "-polylines.geojson"
    ofds_polylines_geojson_file = "output/" + base_name + "-ofds-polylines.geojson"

    # Allow the user to select a folder level from the KML file
    selected_level = choose_folder_level(kml_fullpath)

    # convert kml to geojson
    process_folders(
        kml_fullpath, selected_level, points_geojson_file, polylines_geojson_file
    )
    
    # Load the GeoJSON files into GeoDataFrames
    points_gdf = gpd.read_file(points_geojson_file)
    lines_gdf = gpd.read_file(polylines_geojson_file)
    num_lines = len(lines_gdf)
    print("Number of lines:", num_lines)

    # Snap the points to the nearest line and add basic OFDS metadata
    ofds_points_gdf = snap_all_points(points_gdf, lines_gdf, network_name, network_id, network_links, ofds_points_geojson_file)

    # Iterate over the lines and find the snapped points that intersect each line
    # breaking the lines into segments at the intersection points
    break_polylines_at_nodepoints(ofds_points_gdf, lines_gdf, ofds_points_geojson_file, ofds_polylines_geojson_file, network_name, network_id, network_links)


    
    # plot_results(snapped_points_gdf, spans_gdf)

# main
if __name__ == "__main__":
    main()
    