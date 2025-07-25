"""
This script is used to KML files to the Open Fibre Data Standard format.
It outputs two geojson files, one for network spans and one for nodes.
Author: Steve Song
Email:  steve@manypossibilities.net
License: GPL 3.0
Date: 13-Nov-2024
Usage: python kml2ofds.py
"""

import re
import configparser
from datetime import datetime
import os
import sys
import json
import uuid
import pprint
from collections import Counter
from pykml import parser
import numpy as np
from sklearn.neighbors import KDTree
from shapely.geometry import (
    MultiPolygon,
    Point,
    LineString,
    GeometryCollection,
    MultiPoint,
)
from shapely.geometry import MultiLineString
from shapely.ops import split, nearest_points, unary_union
import geopandas as gpd
import pandas as pd
import click
from libcoveofds.geojson import GeoJSONToJSONConverter, GeoJSONAssumeFeatureType
from libcoveofds.schema import OFDSSchema
from libcoveofds.jsonschemavalidate import JSONSchemaValidator
from libcoveofds.python_validate import PythonValidate
from pathlib import Path

# import matplotlib
# matplotlib.use('Qt5Agg')  # Choose an appropriate backend
# import matplotlib.pyplot as plt


def load_config(config_file):
    config = configparser.ConfigParser()
    config.read(config_file)

    # Get all sections from the config file
    sections = config.sections()

    # Initialize an empty dictionary to store the parsed variables
    parsed_config = {}

    # Iterate over each section
    for section in sections:
        # Get all options (variables) within the section
        options = config.options(section)
        # Iterate over each option
        for option in options:
            # Get the value of the option
            value = config.get(section, option)
            # Assign the value to a variable with the same name
            parsed_config[option] = value

    return parsed_config


def process_kml_file(filename, network_id, network_name, ignore_placemarks):
    with open(filename) as f:
        kml_doc = parser.parse(f).getroot()
    geojson_nodes = []
    geojson_spans = []
    # Start processing from the root Document
    # First look for multiple Documents within the KML file.
    for document in kml_doc.iter("{http://www.opengis.net/kml/2.2}Document"):
        document_name = document.findtext("{http://www.opengis.net/kml/2.2}name")
        print(f"Processing Document: {document_name}")

        nodes, spans = process_document_element(
            document, network_id, network_name, ignore_placemarks
        )
        geojson_nodes.extend(nodes)
        geojson_spans.extend(spans)

    print(f"Number of nodes found before deduplication: {len(geojson_nodes)}")
    geojson_nodes = remove_duplicate_nodes(geojson_nodes, 1)
    print(f"Number of nodes found after deduplication: {len(geojson_nodes)}")

    gdf_nodes = gpd.GeoDataFrame.from_features(geojson_nodes)
    gdf_spans = gpd.GeoDataFrame.from_features(geojson_spans)

    # Test for polylines with only 2 vertices
    # two_vertex_spans = gdf_spans[gdf_spans.geometry.apply(lambda x: len(x.coords) < 5)]
    # if not two_vertex_spans.empty:
    #     print(f"Warning: Found {len(two_vertex_spans)} spans with only 2 vertices.")
    #     print("These spans are:")
    #     print(two_vertex_spans)

    # Save initial GeoJSON objects to files as a temporary measure
    with open("output/nodes.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_nodes}, f)
    with open("output/spans.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_spans}, f)

    snapped_nodes = gdf_nodes.geometry.apply(
        lambda point: snap_to_line(point, gdf_spans)
    )

    # Create a new GeoDataFrame with the snapped points and geojson features
    gdf_ofds_nodes = gpd.GeoDataFrame(
        gdf_nodes.drop(columns="geometry").copy()
    )
    gdf_ofds_nodes["geometry"] = snapped_nodes
    gdf_ofds_nodes.set_geometry("geometry", inplace=True)
    return gdf_ofds_nodes, gdf_spans


def remove_duplicate_nodes(geojson_nodes, precision):
    """
    Removes duplicate nodes from the list of GeoJSON nodes based on a specified precision.
    """
    unique_nodes = []
    seen_hashes = set()
    for node in geojson_nodes:
        # Create a hash based on the rounded coordinates
        node_hash = hash(
            (
                node["properties"]["name"],
                round(node["geometry"]["coordinates"][0], precision),
                round(node["geometry"]["coordinates"][1], precision),
            )
        )
        if node_hash not in seen_hashes:
            # If the hash is not seen before, add the node to the list of unique nodes
            unique_nodes.append(node)
            # Add the hash to the set of seen hashes
            seen_hashes.add(node_hash)
    return unique_nodes


def process_document_element(document, network_id, network_name, ignore_placemarks):
    """Process a KML Document and return a list of GeoJSON nodes and spans.

    Args:
        document (ElementTree.Element): The KML Document to process.

    Returns:
        tuple: A tuple containing two lists of GeoJSON objects. The first list contains GeoJSON nodes (Points),
        and the second list contains GeoJSON spans (LineStrings).
    """
    geojson_nodes = []
    geojson_spans = []

    # Process Folders within the Document
    for folder in document.iter("{http://www.opengis.net/kml/2.2}Folder"):
        # print(f"Found folder: {folder.name.text}")

        # Process Placemarks within this Folder
        for placemark in folder.iter("{http://www.opengis.net/kml/2.2}Placemark"):

            # name = placemark.find('{http://www.opengis.net/kml/2.2}name').text
            name_element = placemark.find("{http://www.opengis.net/kml/2.2}name")
            name = name_element.text if name_element is not None else "Default Name"

            # Check if placemark is a point
            point_geometry = placemark.find("{http://www.opengis.net/kml/2.2}Point")
            if point_geometry is not None:
                # Convert KML Point to Shapely Point
                shapely_point = Point(
                    float(
                        point_geometry.find(
                            "{http://www.opengis.net/kml/2.2}coordinates"
                        ).text.split(",")[0]
                    ),
                    float(
                        point_geometry.find(
                            "{http://www.opengis.net/kml/2.2}coordinates"
                        ).text.split(",")[1]
                    ),
                )
                # Convert Shapely Point to GeoJSON
                node_id = str(uuid.uuid4())
                geojson_node = {
                    "type": "Feature",
                    "properties": {
                        "name": name,
                        "id": node_id,
                        "network": {
                            "id": network_id,
                            "name": network_name,
                            "links": [
                                {
                                    "rel": "describedby",
                                    "href": (
                                        "https://raw.githubusercontent.com/Open-Telecoms-Data/"
                                        "open-fibre-data-standard/0__3__0/schema/network-schema.json"
                                    ),
                                }
                            ],
                        },
                        "featureType": "node",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [shapely_point.x, shapely_point.y],
                    },
                }

                # If name does not match an element in the ignore_placemarks
                # array, add the GeoJSON object to the list
                is_ignored = False
                for ignore_pattern in ignore_placemarks:
                    if re.search(rf"{ignore_pattern}", name):
                        is_ignored = True
                        break
                if not is_ignored:
                    geojson_nodes.append(geojson_node)

            # Look for MultiGeometry elements
            multi_geometry = placemark.find(
                "{http://www.opengis.net/kml/2.2}MultiGeometry"
            )
            if multi_geometry is not None:
                combined_coordinates = []
                # Process LineString elements
                for line_string in multi_geometry.iter(
                    "{http://www.opengis.net/kml/2.2}LineString"
                ):
                    coordinates_text = line_string.find(
                        "{http://www.opengis.net/kml/2.2}coordinates"
                    ).text
                    coordinates = [
                        tuple(map(float, coord.split(",")))
                        for coord in coordinates_text.split()
                    ]
                    combined_coordinates.extend(coordinates)
                # Process Point elements
                for point_elem in multi_geometry.iter(
                    "{http://www.opengis.net/kml/2.2}Point"
                ):
                    coordinates_text = point_elem.find(
                        "{http://www.opengis.net/kml/2.2}coordinates"
                    ).text
                    coords = tuple(map(float, coordinates_text.split(",")[:2]))
                    # Create GeoJSON node for this point
                    node_id = str(uuid.uuid4())
                    geojson_node = {
                        "type": "Feature",
                        "properties": {
                            "name": name,
                            "id": node_id,
                            "network": {
                                "id": network_id,
                                "name": network_name,
                                "links": [
                                    {
                                        "rel": "describedby",
                                        "href": (
                                            "https://raw.githubusercontent.com/Open-Telecoms-Data/"
                                            "open-fibre-data-standard/0__3__0/schema/network-schema.json"
                                        ),
                                    }
                                ],
                            },
                            "featureType": "node",
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [coords[0], coords[1]],
                        },
                    }
                    # If name does not match an element in the ignore_placemarks array, add the GeoJSON object to the list
                    is_ignored = False
                    for ignore_pattern in ignore_placemarks:
                        if re.search(rf"{ignore_pattern}", name):
                            is_ignored = True
                            break
                    if not is_ignored:
                        geojson_nodes.append(geojson_node)
                if len(combined_coordinates) >= 2:
                    shapely_line = LineString(combined_coordinates)
                    if shapely_line is not None:
                        # Convert Shapely LineString to GeoJSON
                        geojson_span = {
                            "type": "Feature",
                            "properties": {
                                "id": "",
                                "name": name,
                                "network": {
                                    "id": network_id,
                                    "name": network_name,
                                    "links": [
                                        {
                                            "rel": "describedby",
                                            "href": (
                                                "https://raw.githubusercontent.com/Open-Telecoms-Data/"
                                                "open-fibre-data-standard/0__3__0/schema/network-schema.json"
                                            ),
                                        }
                                    ],
                                },
                                "featureType": "span",
                            },
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [
                                    (x, y) for x, y, *_ in shapely_line.coords
                                ],
                            },
                        }
                        # Check for duplicates before adding the GeoJSON object to the list
                        is_span_duplicate = any(
                            span["properties"]["name"] == name
                            and span["geometry"]["coordinates"]
                            == geojson_span["geometry"]["coordinates"]
                            for span in geojson_spans
                        )
                        # If not a duplicate, add the GeoJSON object to the list
                        if not is_span_duplicate:
                            geojson_spans.append(geojson_span)
                else:
                    print(
                        f"Warning: Skipping LineString with insufficient points in MultiGeometry: {name}"
                    )

            elif (
                placemark.find("{http://www.opengis.net/kml/2.2}LineString") is not None
            ):
                # Look for LineStrings
                polyline = placemark.find("{http://www.opengis.net/kml/2.2}LineString")
                if polyline is not None:
                    coordinates_text = polyline.find(
                        "{http://www.opengis.net/kml/2.2}coordinates"
                    ).text
                    coordinates = [
                        tuple(map(float, coord.split(",")))
                        for coord in coordinates_text.split()
                    ]
                    # Convert to Shapely LineString
                    # ignore linestrings with only one point
                    if len(coordinates) >= 2:
                        shapely_line = LineString(coordinates)

                    if shapely_line is not None:
                        # Convert Shapely LineString to GeoJSON
                        geojson_span = {
                            "type": "Feature",
                            "properties": {
                                "id": "",
                                "name": name,
                                "network": {
                                    "id": network_id,
                                    "name": network_name,
                                    "links": [
                                        {
                                            "rel": "describedby",
                                            "href": (
                                                "https://raw.githubusercontent.com/Open-Telecoms-Data/"
                                                "open-fibre-data-standard/0__3__0/schema/network-schema.json"
                                            ),
                                        }
                                    ],
                                },
                                "featureType": "span",
                            },
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [
                                    (x, y) for x, y, *_ in shapely_line.coords
                                ],
                            },
                        }
                        # Check for duplicates before adding the GeoJSON object to the list
                        is_span_duplicate = any(
                            span["properties"]["name"] == name
                            and span["geometry"]["coordinates"]
                            == geojson_span["geometry"]["coordinates"]
                            for span in geojson_spans
                        )
                        # If not a duplicate, add the GeoJSON object to the list
                        if not is_span_duplicate:
                            geojson_spans.append(geojson_span)

    # Return the list of GeoJSON objects
    return geojson_nodes, geojson_spans


def snap_to_line(point, lines, tolerance=1e-4):
    """Find the nearest line to a given point and find the
    nearest point on that line to the given point.
    """
    nearest_line = None
    min_distance = float("inf")
    nearest_point_on_line = None

    # Iterate over all lines to find the nearest one and snap the point to it
    for line in lines.geometry:
        # Use nearest_points to get the nearest point on the line to our point
        point_on_line = nearest_points(point, line)[1]
        distance = point.distance(point_on_line)

        if distance < min_distance:
            min_distance = distance
            nearest_line = line
            nearest_point_on_line = point_on_line

    # If the snapped point is close to the start or end of the line, snap to that point within the tolerance
    if nearest_line is not None and nearest_point_on_line is not None:
        start_point = nearest_line.coords[0]
        end_point = nearest_line.coords[-1]
        start_buffer = Point(start_point).buffer(tolerance)
        end_buffer = Point(end_point).buffer(tolerance)

        if nearest_point_on_line.within(start_buffer):
            nearest_point_on_line = Point(start_point)
        elif nearest_point_on_line.within(end_buffer):
            nearest_point_on_line = Point(end_point)

    return nearest_point_on_line


def break_spans_at_node_points(
    gdf_nodes, gdf_spans, network_name, network_id, network_links
):
    """
    Breaks the spans into segments at each node intersection.

    Args:
        gdf_nodes (GeoDataFrame): GeoDataFrame containing the node points.
        gdf_spans (GeoDataFrame): GeoDataFrame containing the spans.
        network_name (str): Name of the network.
        network_id (str): ID of the network.
        network_links (str): Links of the network.

    Returns:
        GeoDataFrame: GeoDataFrame containing the split linestrings.
    """
    split_lines = []
    self_intersects = []
    self_intersect = []
    feature_type = "span"

    # Iterate over the spans and find the nodes that intersect each span
    # breaking the spans into segments at each node intersection
    for _, line_row in gdf_spans.iterrows():
        span_name = line_row["name"]
        buffered_points = []
        intersected_buffered_points = []
        point_names = []
        intersected_points = []

        # Create a buffer around each node point
        for _, point_row in gdf_nodes.iterrows():
            point = point_row.geometry
            buffered_point = point.buffer(1e-9)
            buffered_points.append(buffered_point)

            # Check if the line intersects the buffered point and add the point name to the point_names list
            if line_row.geometry.intersects(buffered_point):
                intersected_buffered_points.append(buffered_point)
                intersected_points.append(point)
                point_name = point_row["name"]
                point_names.append(
                    point_name
                )  # Capture the name of the intersecting point

        # buffered_area = MultiPolygon(intersected_buffered_points)
        buffered_area = MultiPolygon(intersected_buffered_points)

        if line_row.geometry.intersects(buffered_area):
            # Snap each point in splitter to the nearest point on the LineString
            # snapped_points = [snap(point, line_row.geometry, 1.0e-5) for point in intersected_points]
            # buffered_area = MultiPoint(snapped_points)

            # Check for self-intersecting spans
            if line_row.geometry.is_simple:
                split_line = split(line_row.geometry, buffered_area)
            else:
                self_intersect = find_self_intersection(line_row.geometry)
                self_intersects.append(self_intersect)
                split_line = split(line_row.geometry, buffered_area)
                split_line = rejoin_self_intersection_breaks(split_line, self_intersect)

            for segment in split_line.geoms:
                # Check if the segment has more than 2 vertices
                if len(segment.coords) > 2:
                    segment_uuid = str(uuid.uuid4())
                    # Include both polyline and point names with the geometry
                    split_lines.append(
                        (
                            segment_uuid,
                            segment,
                            span_name,
                            feature_type,
                            ", ".join(point_names),
                        )
                    )
        else:
            # Generate a UUID for the original line if no intersection
            if len(line_row.geometry.coords) > 2:
                segment_uuid = str(uuid.uuid4())
                split_lines.append(
                    (segment_uuid, line_row.geometry, span_name, feature_type, "")
                )

    # Create a new GeoDataFrame from the split linestrings
    gdf_spans = gpd.GeoDataFrame(
        split_lines, columns=["id", "geometry", "name", "featureType", "pointNames"]
    )

    # Add network metadata to the split spans GeoDataFrame
    gdf_spans = gdf_spans.apply(
        lambda row: update_network_field(row, network_name, network_id, network_links),
        axis=1,
    )

    gdf_intersects = gpd.GeoDataFrame({"geometry": self_intersects})
    gdf_intersects.set_crs(gdf_spans.crs, inplace=True)
    if not gdf_intersects.empty:
        gdf_intersects.to_file(Path("output/intersects.geojson"), driver="GeoJSON")

    return gdf_spans


def find_self_intersection(line):
    intersection = None
    if not line.is_simple:
        intersection = unary_union(line)
        seg_coordinates = []
        # Only access .geoms if intersection is a GeometryCollection or MultiLineString
        if isinstance(intersection, (GeometryCollection, MultiLineString)):
            for seg in intersection.geoms:
                seg_coordinates.extend(list(seg.coords))
        else:
            seg_coordinates.extend(list(intersection.coords))
        intersection = [Point(p) for p, c in Counter(seg_coordinates).items() if c > 1]
        intersection = MultiPoint(intersection)
    return intersection


def rejoin_self_intersection_breaks(split_lines, intersect_points):

    joined_lines = []
    i = 0

    while i < len(split_lines.geoms):
        current_line = split_lines.geoms[i]

        # Access the next line
        if i + 1 < len(split_lines.geoms):
            next_line = split_lines.geoms[i + 1]
            point_to_check = Point(next_line.coords[0])

            # Check if the last point of line1 is equal to the first point of line2
            if current_line.coords[-1] == next_line.coords[
                0
            ] and intersect_points.contains(point_to_check):

                joined_line = LineString(
                    list(current_line.coords)[:-1] + list(next_line.coords)[1:]
                )
                i += 1  # Increment i by 1 to skip the next line
                current_line = split_lines.geoms[i]
                if i + 1 < len(split_lines.geoms):
                    next_line = split_lines.geoms[i + 1]
                while (
                    current_line.coords[-1] == next_line.coords[0]
                    and intersect_points.contains(Point(next_line.coords[0]))
                    and i + 2 < len(split_lines.geoms)
                ):
                    joined_line = LineString(
                        list(joined_line.coords)[:-1] + list(next_line.coords)[1:]
                    )
                    i += 1
                    current_line = split_lines.geoms[i]
                    next_line = split_lines.geoms[i + 1]

                joined_lines.append(joined_line)
            else:
                joined_lines.append(current_line)
        else:
            joined_lines.append(current_line)

        i += 1  # Increment i by 1 for the next iteration

    geometry_collection = GeometryCollection(joined_lines)
    return geometry_collection


def add_missing_nodes(
    gdf_spans, gdf_nodes, network_id, network_name, network_links, tolerance=1e-6
):
    # Ensure that each segment has a start and end node
    # If not, add the missing nodes to the ofds_points_gdf
    new_nodes = []  # Store new nodes to be appended to the ofds_points_gdf
    for _, row in gdf_spans.iterrows():
        start_point = row.geometry.coords[0]
        end_point = row.geometry.coords[-1]

        # Create buffers around the start and end points
        start_buffer = Point(start_point).buffer(tolerance)
        end_buffer = Point(end_point).buffer(tolerance)

        # Check if start and end points exist in ofds_points_gdf within the buffer
        start_exists = gdf_nodes.geometry.intersects(start_buffer).any()
        end_exists = gdf_nodes.geometry.intersects(end_buffer).any()

        # Add points if they don't exist
        if not start_exists:
            new_node = append_node(start_point, network_id, network_name, network_links)
            if not any(new_node["geometry"] == node["geometry"] for node in new_nodes):
                new_nodes.append(new_node)
        if not end_exists:
            new_node = append_node(end_point, network_id, network_name, network_links)
            if not any(new_node["geometry"] == node["geometry"] for node in new_nodes):
                new_nodes.append(new_node)

    # Convert the list of new nodes into a GeoDataFrame
    if new_nodes:
        # print(new_nodes[:5])
        new_nodes_gdf = gpd.GeoDataFrame.from_features(new_nodes, crs=gdf_nodes.crs)
        combined_gdf_nodes = pd.concat([gdf_nodes, new_nodes_gdf], ignore_index=True)
        # print(
        #     f"Adding {len(new_nodes_gdf)} nodes to a total of {len(combined_gdf_nodes)} nodes"
        # )
    else:
        combined_gdf_nodes = gdf_nodes
        new_nodes_gdf = gpd.GeoDataFrame({col: pd.Series(dtype=gdf_nodes[col].dtype) for col in gdf_nodes.columns})
        new_nodes_gdf = new_nodes_gdf.set_crs(gdf_nodes.crs)

    return combined_gdf_nodes, new_nodes_gdf


def add_nodes_to_spans(gdf_spans, gdf_nodes):

    start_points = []
    end_points = []
    counter = 0

    for _, span in gdf_spans.iterrows():
        start_point_geom = span.geometry.coords[0]
        end_point_geom = span.geometry.coords[-1]

        # Find the point with the same coordinates as the start and end points
        matching_start_point = find_end_point(start_point_geom, gdf_nodes)
        matching_end_point = find_end_point(end_point_geom, gdf_nodes)

        if matching_start_point is not None:
            start_points_info = {
                "id": matching_start_point["id"],
                "name": matching_start_point["name"],
                "location": {
                    "type": "Point",
                    "coordinates": [
                        matching_start_point.geometry.x,
                        matching_start_point.geometry.y,
                    ],
                },
            }
        else:
            start_points_info = None

        if matching_end_point is not None:
            end_points_info = {
                "id": matching_end_point["id"],
                "name": matching_end_point["name"],
                "location": {
                    "type": "Point",
                    "coordinates": [
                        matching_end_point.geometry.x,
                        matching_end_point.geometry.y,
                    ],
                },
            }
        else:
            end_points_info = None

        # Append the matching points information to the lists
        start_points.append(start_points_info)
        end_points.append(end_points_info)
        # Increment the counter and display the progress
        counter += 1
        print(
            f"\rAssociating nodes with spans {counter} of {len(gdf_spans)}",
            end="",
            flush=True,
        )

    # Add the start and end points information to the polylines DataFrame
    gdf_spans["start"] = start_points
    gdf_spans["end"] = end_points

    # Apply conversion to 'start' and 'end' columns in the ofds_spans_gdf DataFrame
    gdf_spans["start"] = gdf_spans["start"].apply(
        lambda x: json.dumps(convert_to_serializable(x)) if x is not None else None
    )
    gdf_spans["end"] = gdf_spans["end"].apply(
        lambda x: json.dumps(convert_to_serializable(x)) if x is not None else None
    )
    return gdf_spans


def merge_nearby_auto_gen_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    # Filter nodes that are auto-generated missing nodes

    filtered_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]
    # Extract coordinates as a 2D array
    coordinates = np.array([(point.x, point.y) for point in filtered_nodes.geometry])

    # Early exit if there are no auto-generated nodes to merge
    if len(coordinates) == 0:
        print("No auto-generated missing nodes to merge.")
        return gdf_ofds_spans, gdf_ofds_nodes

    # Build a KDTree for efficient nearest neighbor search
    tree = KDTree(coordinates)

    # Find pairs of nodes that are within the specified distance
    # The query_radius method returns a list of arrays, one for each point, containing the indices of the neighbors
    # We're only interested in neighbors that are closer than the threshold, so we filter those out
    close_pairs_indices = [
        indices
        for indices in tree.query_radius(coordinates, r=threshold)
        if len(indices) > 1
    ]

    # Flatten the list of lists and remove duplicates
    close_pairs_indices = [
        (i, j)
        for sublist in close_pairs_indices
        for i in sublist
        for j in sublist
        if i != j
    ]
    unique_pairs = list(set((min(i, j), max(i, j)) for i, j in close_pairs_indices))

    # Update the spans with the merged nodes
    merged_node_ids = []
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = json.loads(span["start"])
        end_dict = json.loads(span["end"])

        for pair in unique_pairs:
            if start_dict["id"] == filtered_nodes.iloc[pair[1]]["id"]:
                start_dict["id"] = filtered_nodes.iloc[pair[0]]["id"]
                merged_node_ids.append(filtered_nodes.iloc[pair[1]]["id"])

                # update the span geometry to match the merged node
                new_start_node_geometry = filtered_nodes.iloc[pair[0]]["geometry"]
                span_geometry = span["geometry"]
                updated_coords = list(span_geometry.coords)
                updated_coords[0] = (
                    new_start_node_geometry.x,
                    new_start_node_geometry.y,
                )
                span_geometry = LineString(updated_coords)
                # Assign the updated geometry back to the span
                gdf_ofds_spans.at[index, "geometry"] = span_geometry

            elif end_dict["id"] == filtered_nodes.iloc[pair[1]]["id"]:
                end_dict["id"] = filtered_nodes.iloc[pair[0]]["id"]
                merged_node_ids.append(filtered_nodes.iloc[pair[1]]["id"])

                # update the span geometry to match the merged node
                new_start_node_geometry = filtered_nodes.iloc[pair[0]]["geometry"]
                span_geometry = span["geometry"]
                updated_coords = list(span_geometry.coords)
                updated_coords[-1] = (
                    new_start_node_geometry.x,
                    new_start_node_geometry.y,
                )
                span_geometry = LineString(updated_coords)
                # Assign the updated geometry back to the span
                gdf_ofds_spans.at[index, "geometry"] = span_geometry

        # Convert the updated dictionaries back into JSON strings
        start_json = json.dumps(convert_to_serializable(start_dict))
        end_json = json.dumps(convert_to_serializable(end_dict))

        # Update the 'start' and 'end' columns in the DataFrame for the current row
        gdf_ofds_spans.at[index, "start"] = start_json
        gdf_ofds_spans.at[index, "end"] = end_json

    # Remove nodes that were merged
    # print(merged_node_ids)
    gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(merged_node_ids)]

    print(
        f"Number of nodes after merging nearby auto-added nodes: {len(gdf_ofds_nodes)}"
    )
    return gdf_ofds_spans, gdf_ofds_nodes


def merge_nearby_auto_gen_and_proper_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    # Filter nodes that are auto-generated missing nodes

    # Extract coordinates as a 2D array
    coordinates = np.array([(point.x, point.y) for point in gdf_ofds_nodes.geometry])
    tree = KDTree(coordinates)
    clusters = [
        indices
        for indices in tree.query_radius(coordinates, r=threshold)
        if len(indices) > 1
    ]

    # Filter clusters where at least one node has the name 'Auto generated missing node'
    # making sure that the first cluster element is the node with the name 'Auto generated missing node'
    found_clusters = []
    for cluster in clusters:
        node_names = [gdf_ofds_nodes.iloc[i]["name"] for i in cluster]
        if "Auto generated missing node" in node_names:
            # Determine the index of the node with the name 'Auto generated missing node'
            auto_generated_index = node_names.index("Auto generated missing node")
            # Ensure the first ID in the pair is the ID of the node with the specified name
            if auto_generated_index != 0:
                # Swap the order of the group if necessary to make the auto-generated node first
                cluster = [cluster[auto_generated_index]] + [
                    i for i in cluster if i != auto_generated_index
                ]
            found_clusters.append(cluster)

    # Update the spans with the merged nodes
    merged_node_ids = []
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = json.loads(span["start"])
        end_dict = json.loads(span["end"])

        for cluster in found_clusters:
            if start_dict["id"] == gdf_ofds_nodes.iloc[cluster[0]]["id"]:
                start_dict["id"] = gdf_ofds_nodes.iloc[cluster[1]]["id"]
                merged_node_ids.append(gdf_ofds_nodes.iloc[cluster[0]]["id"])

                # update the span geometry to match the merged node
                new_start_node_geometry = gdf_ofds_nodes.iloc[cluster[1]]["geometry"]
                span_geometry = span["geometry"]
                updated_coords = list(span_geometry.coords)
                updated_coords[0] = (
                    new_start_node_geometry.x,
                    new_start_node_geometry.y,
                )
                span_geometry = LineString(updated_coords)
                # Assign the updated geometry back to the span
                gdf_ofds_spans.at[index, "geometry"] = span_geometry

            elif end_dict["id"] == gdf_ofds_nodes.iloc[cluster[0]]["id"]:
                end_dict["id"] = gdf_ofds_nodes.iloc[cluster[1]]["id"]
                merged_node_ids.append(gdf_ofds_nodes.iloc[cluster[0]]["id"])

                # update the span geometry to match the merged node
                new_start_node_geometry = gdf_ofds_nodes.iloc[cluster[1]]["geometry"]
                span_geometry = span["geometry"]
                updated_coords = list(span_geometry.coords)
                updated_coords[-1] = (
                    new_start_node_geometry.x,
                    new_start_node_geometry.y,
                )
                span_geometry = LineString(updated_coords)
                # Assign the updated geometry back to the span
                gdf_ofds_spans.at[index, "geometry"] = span_geometry

        # Convert the updated dictionaries back into JSON strings
        start_json = json.dumps(convert_to_serializable(start_dict))
        end_json = json.dumps(convert_to_serializable(end_dict))

        # Update the 'start' and 'end' columns in the DataFrame for the current row
        gdf_ofds_spans.at[index, "start"] = start_json
        gdf_ofds_spans.at[index, "end"] = end_json

    # Remove nodes that were merged
    # print(merged_node_ids)
    gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(merged_node_ids)]

    print(
        f"Number of nodes after merging nearby auto-added nodes near proper nodes: {len(gdf_ofds_nodes)}"
    )
    return gdf_ofds_spans, gdf_ofds_nodes


def join_node_terminating_near_span(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    # Filter nodes that are auto-generated missing nodes
    print(f"Total number of nodes: {len(gdf_ofds_nodes)}")

    # Function to safely extract ID from a dictionary or string
    def extract_id(x):
        if isinstance(x, dict):
            return x.get("id")
        elif isinstance(x, str):
            try:
                return json.loads(x).get("id")
            except json.JSONDecodeError:
                return x
        return x

    # Extract start and end IDs
    start_ids = gdf_ofds_spans["start"].apply(extract_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_id)

    # Combine start and end IDs
    all_ids = pd.concat([start_ids, end_ids])
    # Count occurrences of each ID
    id_counts = all_ids.value_counts()
    print("Number of unique IDs in spans:", len(id_counts))
    print("IDs that appear only once:", sum(id_counts == 1))
    print("Sample of id_counts:")
    print(id_counts.head())

    #  Find IDs that appear only once
    single_occurrence_ids = id_counts[id_counts == 1].index
    # Filter gdf_ofds_nodes
    filtered_nodes = gdf_ofds_nodes[gdf_ofds_nodes["id"].isin(single_occurrence_ids)]

    print("\nNumber of filtered nodes:", len(filtered_nodes))

    # Print the name and ID for each filtered node
    print("\nFiltered Nodes (Name and ID):")
    for _, node in filtered_nodes.iterrows():
        print(f"Name: {node['name']}, ID: {node['id']}")

    # Check for partial matches
    def find_partial_matches(node_id, span_ids):
        return any(str(node_id) in str(span_id) for span_id in span_ids)


def find_end_point(span_endpoint, gdf_nodes, tolerance=1e-3):
    point_geom = Point(span_endpoint)
    # Create a buffer around the point with the specified tolerance
    buffered_point = point_geom.buffer(tolerance)
    # Filter points that are within the buffer
    matched_points = gdf_nodes[gdf_nodes.geometry.within(buffered_point)]

    if not matched_points.empty:
        # Calculate distances from the endpoint to each matched point
        distances = matched_points.geometry.apply(
            lambda geom: point_geom.distance(geom)
        )
        # Find the index of the point with the minimum distance
        closest_point_index = distances.idxmin()
        # Return the closest matched point
        return matched_points.loc[closest_point_index]
    else:
        return None  # Return None if no match is found


def append_node(new_node_coords, network_id, network_name, network_links):
    # Returns a GeoJSON feature dictionary representing the new node
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": new_node_coords},
        "properties": {
            "id": str(uuid.uuid4()),  # Generate a new UUID for the id
            "name": "Auto generated missing node",
            "network": {"id": network_id, "name": network_name, "links": network_links},
            "featureType": "node",
        },
    }


def update_network_field(row, network_name, network_id, network_links):
    """Updates the 'network' field in the row's dictionary
    with 'id', 'name', and 'links' keys."""

    if "network" not in row:
        # If 'network' does not exist, create it as a dictionary
        row["network"] = {}

    # Update 'id' and 'name' in the 'network' dictionary
    row["network"]["id"] = network_id
    row["network"]["name"] = network_name
    row["network"]["links"] = network_links

    return row


def convert_to_serializable(obj):
    """Converts a dictionary to JSON, ensuring all numeric values are Python native types."""
    if isinstance(obj, dict):
        return {key: convert_to_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(element) for element in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        return obj


@click.command(help="Convert KML files to the Open Fibre Data Standard format.")
@click.option(
    "--network-profile",
    required=True,
    help="Path to the network profile configuration file (required).",
    type=click.Path(exists=True),
)
def main(network_profile):
    """Convert KML files to the Open Fibre Data Standard format.
    
    This script takes a KML file and converts it to the Open Fibre Data Standard format,
    outputting both GeoJSON and JSON files for network nodes and spans.
    
    The script requires a network profile configuration file that specifies:
    - KML file name
    - Network name and ID
    - Network links
    - Input/output directories
    - Any placemarks to ignore
    """
    print(f"Running with network_profile: {network_profile}")

    # config_file = "kml2ofds.ini"
    network_prof = load_config(network_profile)

    if not network_prof["kml_file_name"]:
        print("Error. Please set kml file name in network profile")
        sys.exit(1)
    else:
        kml_file = network_prof["kml_file_name"]

    # set network name,id, and links
    if not network_prof["network_name"]:
        network_name = "Default Network Name"
        print("Network name not found in config file. Using default value.")
    else:
        network_name = network_prof["network_name"]

    if not network_prof["network_id"]:
        network_id = str(uuid.uuid4())
    else:
        network_id = network_prof["network_id"]

    if not network_prof["network_links"]:
        network_link_url = "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json"
        print("Network links not found in config file. Using default value.")
    else:
        network_link_url = network_prof["network_links"]

    network_links = [{"rel": "describedby", "href": network_link_url}]

    if not network_prof["ignore_placemarks"]:
        ignore_placemarks = []
    else:
        ignore_placemarks = network_prof["ignore_placemarks"].split(";")

    # output files
    today = datetime.today()
    date_string = today.strftime("%d%b%Y").lower()

    if not network_prof["input_directory"]:
        input_directory = "input/"
    else:
        input_directory = network_prof["input_directory"]

    if not network_prof["output_directory"]:
        output_directory = "output/"
    else:
        output_directory = network_prof["output_directory"]

    # Check if input_directory exists, if not, create it
    if not os.path.exists(input_directory):
        os.makedirs(input_directory)

    # Check if output_directory exists, if not, create it
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    directory = os.path.join(os.getcwd(), input_directory)
    kml_fullpath = os.path.join(directory, kml_file)

    # set file names
    network_filename_normalised = kml_file.replace(" ", "_").upper()

    if not network_prof["output_name_prefix"]:
        output_name_prefix = network_filename_normalised[3:]
    else:
        output_name_prefix = network_prof["output_name_prefix"]

    nodes_ofds_output = (
        output_directory
        + output_name_prefix
        + "_ofds-nodes_"
        + date_string
        + ".geojson"
    )
    # print(nodes_ofds_output)

    spans_ofds_output = (
        output_directory
        + output_name_prefix
        + "_ofds-spans_"
        + date_string
        + ".geojson"
    )

    ofds_json_output = (
        output_directory + output_name_prefix + "_ofds-json_" + date_string + ".json"
    )

    # Basic parsing of KML file into a set of nodes and spans, adjusting nodes to snap to spans
    gdf_ofds_nodes, gdf_spans = process_kml_file(
        kml_fullpath, network_id, network_name, ignore_placemarks
    )

    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(
        f"Breaking spans at node points. \nBefore: {len(gdf_spans)} spans, smallest span {min_vert}"
    )
    gdf_spans = break_spans_at_node_points(
        gdf_ofds_nodes, gdf_spans, network_name, network_id, network_links
    )
    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(f" After: {len(gdf_spans)} spans, smallest span {min_vert}\n")

    # Check for any spans that do not have a node at the start or end point and add as needed
    gdf_ofds_nodes, gdf_auto_gen_nodes = add_missing_nodes(
        gdf_spans, gdf_ofds_nodes, network_id, network_name, network_links
    )
    # Add information on the start and end nodes to the spans
    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(
        f"Adding nodes to spans. \nBefore: {len(gdf_spans)} spans, smallest span {min_vert}"
    )
    gdf_ofds_spans = add_nodes_to_spans(gdf_spans, gdf_ofds_nodes)
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(f"\n After: {len(gdf_ofds_spans)} spans, smallest span {min_vert}\n")

    # Merge nearby auto-generated nodes that are in close proximity to each other
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(
        f"Merging nearby autogen. \nBefore: {len(gdf_ofds_spans)} spans, smallest span {min_vert}"
    )
    gdf_ofds_spans, gdf_ofds_nodes = merge_nearby_auto_gen_nodes(
        gdf_ofds_nodes, gdf_ofds_spans, 1e-1
    )
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(f" After: {len(gdf_ofds_spans)} spans, smallest span {min_vert}\n")

    # Merge nearby auto-generated nodes that are in close proximity propoer nodes
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(
        f"Merging nearby autogen and proper nodes. \nBefore: {len(gdf_ofds_spans)} spans, smallest span {min_vert}"
    )
    gdf_ofds_spans, gdf_ofds_nodes = merge_nearby_auto_gen_and_proper_nodes(
        gdf_ofds_nodes, gdf_ofds_spans, 1e-3
    )
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(f" After: {len(gdf_ofds_spans)} spans, smallest span {min_vert}\n")

    # join_node_terminating_near_span(gdf_ofds_nodes,gdf_ofds_spans,1e-1)

    # Save the results to geojson files
    gdf_ofds_spans.to_file(spans_ofds_output, driver="GeoJSON")
    gdf_ofds_nodes.to_file(nodes_ofds_output, driver="GeoJSON")

    # ofds_spans_geojson = json.loads(gdf_ofds_spans.to_json(indent=None))
    # ofds_nodes_geojson = json.loads(gdf_ofds_nodes.to_json(indent=None))

    with open(spans_ofds_output, "r") as file:
        ofds_spans_geojson = json.load(file)
    with open(nodes_ofds_output, "r") as file:
        ofds_nodes_geojson = json.load(file)

    worker = GeoJSONToJSONConverter()
    worker.process_data(
        ofds_nodes_geojson, assumed_feature_type=GeoJSONAssumeFeatureType.NODE
    )
    worker.process_data(
        ofds_spans_geojson, assumed_feature_type=GeoJSONAssumeFeatureType.SPAN
    )

    ofds_json = worker.get_json()

    # Write the dictionary to a JSON file
    try:
        with open(ofds_json_output, "w", encoding="utf-8") as json_file:
            json.dump(ofds_json, json_file, indent=4, ensure_ascii=False)
    except IOError as e:
        raise IOError(f"Error writing to file {ofds_json_output}: {e}")

    # schema = OFDSSchema()
    # validator = PythonValidate(schema)
    # result = validator.validate(ofds_json)

    # if not result:
    #     print("Validation successful")
    # else:
    #     print("Validation failed")
    #     for error in result:
    #         pprint.pprint(error)

    print("Complete")


# main
if __name__ == "__main__":
    try:
        main()
    except click.exceptions.MissingParameter:
        main(['--help'])
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()

