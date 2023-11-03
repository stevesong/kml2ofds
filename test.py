import xml.etree.ElementTree as ET


def parse_kml_file(kml_file):
    points = []
    lines = []

    try:
        tree = ET.parse(kml_file)
        root = tree.getroot()

        # Find all Placemark elements
        placemarks = root.findall(".//{http://www.opengis.net/kml/2.2}Placemark")

        for placemark in placemarks:
            # Extract the name of the Placemark (optional)
            name = (
                placemark.find(".//{http://www.opengis.net/kml/2.2}name").text
                if placemark.find(".//{http://www.opengis.net/kml/2.2}name") is not None
                else ""
            )

            # Extract the coordinates within the Placemark
            coordinates = placemark.find(
                ".//{http://www.opengis.net/kml/2.2}coordinates"
            ).text.split()

            # Check if the Placemark contains a LineString or a Point
            line_element = placemark.find(
                ".//{http://www.opengis.net/kml/2.2}LineString"
            )
            point_element = placemark.find(".//{http://www.opengis.net/kml/2.2}Point")

            if line_element is not None:
                # This Placemark contains a LineString
                lines.append({"name": name, "coordinates": coordinates})
            elif point_element is not None:
                # This Placemark contains a Point
                points.append({"name": name, "coordinates": coordinates})

    except ET.ParseError as e:
        print(f"Error parsing the KML file: {str(e)}")

    return points, lines


# Example usage
if __name__ == "__main__":
    kml_file = "your_kml_file.kml"
    point_data, line_data = parse_kml_file(kml_file)

    print("Points:")
    for point in point_data:
        print(f"Name: {point['name']}, Coordinates: {point['coordinates']}")

    print("Lines:")
    for line in line_data:
        print(f"Name: {line['name']}, Coordinates: {line['coordinates']}")
