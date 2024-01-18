from fastkml import kml
from shapely.geometry import LineString

def parse_kml_file(kml_file_path):
    with open(kml_file_path, 'rt') as file:
        k = kml.KML()
        k.from_string(file.read())
        features = list(k.features())
        folders = [f for f in features if isinstance(f, kml.Folder)]
        folder_linestrings = []
        for folder in folders:
            folder_linestrings = []
            for feature in folder.features():
                if isinstance(feature.geometry, LineString):
                    folder_linestrings.append(feature.geometry)
            if folder_linestrings:
                folder.geometry = folder_linestrings
                folder_name = folder.name
                yield folder_name, folder
                
                
# usage
if __name__ == "__main__":
    kml_file = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    parse_kml_file(kml_file)
                