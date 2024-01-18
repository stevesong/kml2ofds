import geojson
from kml2geojson import convert
global geojson_data

def print_to_file(geojson_data, geojson_file_path):
    # Write GeoJSON to a file
    with open(geojson_file_path, 'w', encoding='utf-8') as geojson_file:
        geojson.dump(geojson_data, geojson_file, ensure_ascii=False, indent=2)

def kml_to_geojson(kml_file_path):
    # Convert KML to GeoJSON
    geojson_data = convert(kml_file_path)

    # Convert GeoJSON to a string and prettify it
    geojson_string = geojson.dumps(geojson_data, ensure_ascii=False, indent=2)
    
    # Remove opening and closing square brackets due to apparent bug in kml2geojson
    geojson_string = geojson_string.strip()[1:-1]

    # Convert GeoJSON string back to a dictionary
    geojson_data = geojson.loads(geojson_string)

    print_to_file(geojson_data, geojson_file_path)

    return geojson_data

if __name__ == "__main__":
    kml_file_path = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    geojson_file_path = "MTN.geojson"

    kml_to_geojson(kml_file_path)
    
    
    
    
    
    