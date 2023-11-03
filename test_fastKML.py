from fastkml import kml


def parse_kml_file(kml_file):
    points = []
    lines = []

    with open(kml_file, "r") as f:
        k = kml.KML()
        kml_doc = f.read()
        k.from_string(kml_doc)

        for placemark in k.features():
            name = placemark.name if hasattr(placemark, "name") else ""
            coordinates = []

            if hasattr(placemark, "geometry"):
                if isinstance(placemark.geometry, kml.Placemark.Point):
                    coordinates = [placemark.geometry.coords]
                elif isinstance(placemark.geometry, kml.Placemark.LineString):
                    coordinates = placemark.geometry.coords

            if coordinates:
                if isinstance(coordinates[0], tuple):
                    coordinates = [coordinates]

                if isinstance(coordinates, list):
                    coordinates = [
                        [coord for coord in coords] for coords in coordinates
                    ]

            if coordinates:
                if isinstance(coordinates[0], list):
                    coordinates = [
                        ",".join(
                            [
                                "{:.6f},{:.6f}".format(coord[0], coord[1])
                                for coord in coords
                            ]
                        )
                        for coords in coordinates
                    ]

            line_element = isinstance(placemark.geometry, kml.Placemark.LineString)
            point_element = isinstance(placemark.geometry, kml.Placemark.Point)

            if line_element:
                lines.append({"name": name, "coordinates": coordinates})
            elif point_element:
                points.append({"name": name, "coordinates": coordinates})

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
