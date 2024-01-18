from fastkml import kml


def parse_kml_file(file_path):
    with open(file_path, 'rt', encoding='utf-8') as kml_file:
        kml_document = kml_file.read().encode('utf-8')

    k = kml.KML()
    kml_doc = k.from_string(kml_document)

    if kml_doc is not None:
        # Iterate through features (placemarks, folders, etc.) in the KML document
        for feature in kml_doc.features():
            process_feature(feature)
    else:
        print("Error parsing KML file")

def process_feature(feature):
    # Handle each feature (e.g., print its name)
    print("Feature Name:", feature.name)

    # If the feature has sub-features (e.g., subfolders), recursively process them
    if isinstance(feature, kml.Folder):
        for sub_feature in feature.features():
            process_feature(sub_feature)



# Example usage
if __name__ == "__main__":
    kml_file = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    parse_kml_file(kml_file)
