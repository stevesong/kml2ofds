from pykml import parser
import inquirer 
import json
from shapely.geometry import Point, LineString, shape, mapping
from shapely.ops import split, nearest_points
from functools import partial
import pyproj
from pyproj import Transformer
from shapely.ops import transform


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

    return points, polylines

def apply_africa_sinusoidal_projection(points_file, polylines_file, output_points_file, output_polylines_file):
    # Define the Africa Sinusoidal projection
    africa_sinusoidal_proj = pyproj.Proj('+proj=sinu +lon_0=15 +x_0=0 +y_0=0 +a=6378137 +b=6378137 +units=m +no_defs')

    # For the transformation, use CRS objects. The source is EPSG:4326.
    source_crs = pyproj.CRS("epsg:4326")  # Geographic coordinate system WGS 84
    target_crs = pyproj.CRS(africa_sinusoidal_proj.srs)  # Target CRS from the custom projection

    # Create a Transformer object for converting from source_crs to target_crs
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

    # Load GeoJSON files
    points_data = load_geojson(points_file)
    polylines_data = load_geojson(polylines_file)

    # Transform points
    for feature in points_data['features']:
        point = shape(feature['geometry'])
        # Extract x and y coordinates (longitude and latitude) from the point
        x, y = point.x, point.y

        # Use the transformer to transform the coordinates
        transformed_x, transformed_y = transformer.transform(x, y)

        # Create a new Shapely Point with the transformed coordinates
        transformed_point = Point(transformed_x, transformed_y)
        feature['geometry'] = mapping(transformed_point)

    # Transform polylines
    for feature in polylines_data['features']:
        polyline = shape(feature['geometry'])
        # List to hold the transformed points
        transformed_points = []

        # Iterate over each point in the polyline
        for x, y in polyline.coords:
            # Transform each point and add it to the list of transformed points
            transformed_x, transformed_y = transformer.transform(x, y)
            transformed_points.append((transformed_x, transformed_y))

        # Create a new LineString from the transformed points
        transformed_polyline = LineString(transformed_points)
        feature['geometry'] = mapping(transformed_polyline)

    # Save the transformed geometries back to new GeoJSON files
    save_geojson(points_data, output_points_file)
    save_geojson(polylines_data, output_polylines_file)

def split_polylines(points_file, polylines_file, output_file, distance_threshold=100):
    # Load GeoJSON files
    with open(polylines_file, 'r') as file:
        polylines_data = json.load(file)
    with open(points_file, 'r') as file:
        points_data = json.load(file)

    modified_polylines = []

    for feature in polylines_data['features']:
        polyline = shape(feature['geometry'])
        for point_feature in points_data['features']:
            point = shape(point_feature['geometry'])
            # print(f"Processing point: {point}  Distance: {point.distance(polyline)}")
            
            if point.distance(polyline) <= distance_threshold:
                print(f"Found point: {point}  Distance: {point.distance(polyline)}")

                # Find the nearest point on the polyline to our point
                nearest = nearest_points(point, polyline)[1]
                
                # Create a small line segment (perpendicular to the polyline) to use as splitter
                # The segment must be small but should cross the polyline
                splitter = LineString([nearest, nearest.buffer(0.0001).exterior.coords[0]])
                
                # Split the line at the nearest point
                split_line = split(polyline, splitter)

                # Add the split lines to our list
                for geom in split_line.geoms:
                    if geom.geom_type == 'LineString':
                        modified_polylines.append(geom)
            else:
                modified_polylines.append(polyline)

    # Convert the modified polylines back to GeoJSON
    modified_geojson = {"type": "FeatureCollection", "features": []}
    for line in modified_polylines:
        feature = {
            "type": "Feature",
            "properties": {},
            "geometry": mapping(line)
        }
        modified_geojson['features'].append(feature)
        
    # Save the modified polylines to a new GeoJSON file
    with open(output_file, 'w') as file:
        json.dump(modified_geojson, file)



def load_geojson(filename):
    with open(filename, 'r') as file:
        return json.load(file)

def save_geojson(data, filename):
    with open(filename, 'w') as file:
        json.dump(data, file)



# main
if __name__ == "__main__":
    # set defaults
    max_distance = 500 # meters
    
    kml_filename = "input/MTN-Ghana-FOB-export.kml"

    points_geojson_file = "output/points.geojson"
    polylines_geojson_file = "output/polylines.geojson"

    points_geojson_af_sin_file = "output/points-afsin.geojson"
    polylines_geojson_af_sin_file = "output/polylines-afsin.geojson"

    ofds_polylines_geojson_file = "output/ofds-polylines.geojson"

    selected_level = choose_folder_level(kml_filename)

    # convert kml to geojson
    process_folders(
        kml_filename, selected_level, points_geojson_file, polylines_geojson_file
    )
    
    # convert geojson to africa sinusoidal projection
    apply_africa_sinusoidal_projection(points_geojson_file, polylines_geojson_file, points_geojson_af_sin_file, polylines_geojson_af_sin_file)
    
    split_polylines(points_geojson_af_sin_file, polylines_geojson_af_sin_file, ofds_polylines_geojson_file, max_distance)