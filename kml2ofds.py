from pykml import parser
import inquirer 
import json
from shapely.geometry import MultiPolygon
from shapely.ops import split, nearest_points
import geopandas as gpd
import uuid


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

# main
if __name__ == "__main__":
            
    kml_filename = "input/MTN-Ghana-FOB-export.kml"
    points_geojson_file = "output/points.geojson"
    polylines_geojson_file = "output/polylines.geojson"
    ofds_polylines_geojson_file = "output/ofds-polylines.geojson"

    selected_level = choose_folder_level(kml_filename)

    # convert kml to geojson
    process_folders(
        kml_filename, selected_level, points_geojson_file, polylines_geojson_file
    )
    
    # Load the GeoJSON files into GeoDataFrames
    points_gdf = gpd.read_file("output/points.geojson")
    lines_gdf = gpd.read_file("output/polylines.geojson")
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
    # snapped_points_gdf.to_file('output/snapped_points.geojson', driver='GeoJSON')

    split_lines = []
    
    # Iterate over the lines and find the snapped points that intersect each line
    for idx, line_row in lines_gdf.iterrows():
        polyline_name = line_row['name']
        buffered_points = []
        point_names = []
        
        for point_idx, point_row in snapped_points_gdf.iterrows():
            point = point_row.geometry
            point_name = point_row['name']
            buffered_point = point.buffer(1e-5)
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

    # fig, ax = plt.subplots()
    # split_linestrings_gdf.plot(ax=ax, color='blue')
    # snapped_points_gdf.plot(ax=ax, color='red', markersize=5)
    # plt.show()

    # # Save the split lines to a new GeoJSON file
    split_linestrings_gdf.to_file(ofds_polylines_geojson_file, driver='GeoJSON')
    # print(split_linestrings_gdf.head())