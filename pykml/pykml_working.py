from pykml import parser


def parse_kml_file(filename):
    """
    Parses a KML file with multiple subfolders and extracts points and polylines into separate objects for each subfolder.

    Args:
      filename: Path to the KML file.

    Returns:
      A dictionary where keys are folder names and values are dictionaries containing two lists:
      * "PoPs": A list of dictionaries with point information (name, description, coordinates).
      * "segments": A list of dictionaries with polyline information (name, description, coordinates).
    """
    folder_data = {}
    with open(filename) as f:
        root = parser.parse(f).getroot()
        for folder in root.Document.Folder:
            # Initialize empty lists for points and polylines
            pops = []
            segments = []
            try:
                # Attempt to access placemarks in current folder
                for placemark in folder.Placemark:
                    name = placemark.name
                    description = placemark.description

                    # Check for Point geometry
                    if placemark.geometry.type == "Point":
                        coordinates = placemark.get_coordinates()
                        pops.append(
                            {
                                "name": name,
                                "description": description,
                                "coordinates": coordinates,
                            }
                        )
                    # Check for LineString geometry
                    elif placemark.geometry.type == "LineString":
                        coordinates = placemark.get_coordinates()
                        segments.append(
                            {
                                "name": name,
                                "description": description,
                                "coordinates": coordinates,
                            }
                        )
            except AttributeError as e:
                print(f"Error parsing folder '{folder.name}': {e}")
                # Add collected data to dictionary
                folder_data[folder.name] = {"PoPs": pops, "segments": segments}

                # Attempt to parse subfolders within the current folder
                for subfolder in folder.Folder:
                    try:
                        # Initialize empty lists for subfolder points and polylines
                        sub_pops = []
                        sub_segments = []
                        for subplacemark in subfolder.Placemark:
                            subname = subplacemark.name
                            print(subname)
                            subdescription = subplacemark.description

                            # Check for Point geometry
                            if subplacemark.geometry.type == "Point":
                                subcoordinates = subplacemark.get_coordinates()
                                sub_pops.append(
                                    {
                                        "name": subname,
                                        "description": subdescription,
                                        "coordinates": subcoordinates,
                                    }
                                )
                            # Check for LineString geometry
                            elif subplacemark.geometry.type == "LineString":
                                subcoordinates = subplacemark.get_coordinates()
                                sub_segments.append(
                                    {
                                        "name": subname,
                                        "description": subdescription,
                                        "coordinates": subcoordinates,
                                    }
                                )
                        # Add collected subfolder data to existing folder data
                        folder_data[folder.name]["PoPs"].extend(sub_pops)
                        folder_data[folder.name]["segments"].extend(sub_segments)
                    except AttributeError:
                        # Ignore errors within subfolders
                        pass

    return folder_data


# usage
if __name__ == "__main__":
    filename = (
        "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    )

    folder_data = parse_kml_file(filename)

    # Access folder data
    for folder_name, data in folder_data.items():
        print(f"Folder: {folder_name}")

        # Access points and polylines for this folder
        print("   PoPs:")
        for pop in data["PoPs"]:
            print(f"    - {pop['name']}")
        print("   Segments:")
        for segment in data["segments"]:
            print(f"    - {segment['name']}")
