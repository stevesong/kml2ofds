from pykml import parser
import os
import inquirer 
import json
from shapely.geometry import MultiPolygon
from shapely.ops import split, nearest_points
import geopandas as gpd
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

def process_folders_recursive(folder, level, selected_level, points, polylines):
    if hasattr(folder, "name"):
        if level >= selected_level:
            print(f"Processing Level {level}: {folder.name}")
            process_placemarks(getattr(folder, 'Placemark', []), level, selected_level, points, polylines)

        for child_folder in getattr(folder, 'Folder', []):
            process_folders_recursive(child_folder, level + 1, selected_level, points, polylines)

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

def load_geojson(filename):
    with open(filename, 'r') as file:
        return json.load(file)

def save_geojson(data, filename):
    with open(filename, 'w') as file:
        json.dump(data, file)

# Function to generate a random color
def random_color():
    return "#" + ''.join([random.choice('0123456789ABCDEF') for j in range(6)])

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
    points_geojson_file = "output/" + base_name + "-points.geojson"
    snapped_points_geojson_file = "output/" + base_name + "-snapped_points.geojson"
    polylines_geojson_file = "output/" + base_name + "-polylines.geojson"
    ofds_polylines_geojson_file = "output/" + base_name + "-ofds-polylines.geojson"

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

    # Ensure CRS match, if not, reproject
    if points_gdf.crs != lines_gdf.crs:
        points_gdf = points_gdf.to_crs(lines_gdf.crs)
        
    # Apply the snap_to_line function to each point in the points GeoDataFrame
    snapped_points = points_gdf.geometry.apply(lambda point: snap_to_line(point, lines_gdf))
    print("Total number of snapped points:", snapped_points.size)
    
    # Create a new GeoDataFrame with the snapped points
    snapped_points_gdf = gpd.GeoDataFrame(points_gdf.drop(columns='geometry'), geometry=snapped_points, crs=points_gdf.crs)

    # Save the snapped points dataframe to a new GeoJSON file
    snapped_points_gdf.to_file(snapped_points_geojson_file , driver='GeoJSON')

    split_lines = []
    
    # Iterate over the lines and find the snapped points that intersect each line
    for idx, line_row in lines_gdf.iterrows():
        polyline_name = line_row['name']
        buffered_points = []
        point_names = []
        
        for point_idx, point_row in snapped_points_gdf.iterrows():
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
                split_lines.append((segment_uuid, segment, polyline_name, ", ".join(point_names)))
        else:
            # Generate a UUID for the original line if no intersection
            segment_uuid = str(uuid.uuid4())
            split_lines.append((segment_uuid, line_row.geometry, polyline_name, ""))

    print("Number of segments found:", len(split_lines))        

    # Create a new GeoDataFrame from the split linestrings
    split_linestrings_gdf = gpd.GeoDataFrame(split_lines, columns=['uuid', 'geometry', 'polyline_name', 'point_names'], crs=lines_gdf.crs)

    # Set the 'uuid' column as the index of the GeoDataFrame
    split_linestrings_gdf.set_index('uuid', inplace=True)

    # Save the split lines to a new GeoJSON file
    split_linestrings_gdf.to_file(ofds_polylines_geojson_file, driver='GeoJSON')
    # print(split_linestrings_gdf.head())
    
    # ##  Plot the snapped points and split lines
    # fig, ax = plt.subplots()
    # # Iterate through each row and plot with a random color
    # for _, row in split_linestrings_gdf.iterrows():
    #     random_color = "#" + ''.join([random.choice('0123456789ABCDEF') for j in range(6)])
    #     ax.plot(*row.geometry.xy, color=random_color)

    # # Plot the points
    # snapped_points_gdf.plot(ax=ax, color='red', markersize=5)

    # plt.show()

# main
if __name__ == "__main__":
    main()
    