from pykml import parser
# from prompt_toolkit import PromptSession
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.application import get_app
# from prompt_toolkit.key_binding.key_processor import KeyPressEvent
# from prompt_toolkit.validation import Validator, ValidationError
import json
import inquirer 

def get_folder_levels(folder, level=1):
    levels = []
    if hasattr(folder, 'name'):
        levels.append((f"{'    ' * level}Folder Level {level}: {folder.name}", level))

    for child_folder in getattr(folder, 'Folder', []):
        levels.extend(get_folder_levels(child_folder, level + 1))

    return levels

def select_level(levels):
    questions = [
        inquirer.List('level',
                      message="Select a folder level:",
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

    for level in levels:
        print(level[0])

    user_choice = select_level(levels)
    # user_choice = prompt('Select a folder level: ', validator=LevelValidator())
    print(f"\nSelected folder level: {user_choice}. Processing...")

    return user_choice

def parse_coordinates(coordinates_text, geom_type):
    if geom_type == "Point":
        return list(map(float, coordinates_text.split(",")))
    elif geom_type == "LineString":
        return [tuple(map(float, point.split(","))) for point in coordinates_text.split()]

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



# main
if __name__ == "__main__":
    filename = (
        "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    )
    points_geojson_file_path = (
        "/home/steve/Documents/OpenTelecomData/kml2ofds/pykml/points.geojson"
    )
    polylines_geojson_file_path = (
        "/home/steve/Documents/OpenTelecomData/kml2ofds/pykml/polylines.geojson"
    )

    selected_level = choose_folder_level(filename)
    process_folders(
        filename, selected_level, points_geojson_file_path, polylines_geojson_file_path
    )
