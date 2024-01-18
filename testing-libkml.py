from pykml import parser


def parse_kml_file(kml_file):
    points = []
    lines = []

    with open(kml_file, "r") as f:
        root = parser.parse(f).getroot()

        for placemark in root.Document.Folder.Placemark:
            name = placemark.name.text if hasattr(placemark, "name") else ""
            coordinates = (
                placemark.Point.coordinates.text if hasattr(placemark, "Point") else ""
            )

            if coordinates:
                coordinates = coordinates.strip().split(",")
                coordinates = [coord.strip() for coord in coordinates]

            line_element = placemark.LineString
            point_element = placemark.Point

            if line_element is not None:
                lines.append({"name": name, "coordinates": coordinates})
            elif point_element is not None:
                points.append({"name": name, "coordinates": coordinates})

    return points, lines


# Example usage
if __name__ == "__main__":
    kml_file = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"
    point_data, line_data = parse_kml_file(kml_file)

    print("Points:")
    for point in point_data:
        print(f"Name: {point['name']}, Coordinates: {point['coordinates']}")

    print("Lines:")
    for line in line_data:
        print(f"Name: {line['name']}, Coordinates: {line['coordinates']}")
