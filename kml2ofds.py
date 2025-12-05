"""
This script is used to KML files to the Open Fibre Data Standard format.
It outputs two geojson files, one for network spans and one for nodes.
Author: Steve Song
Email:  steve@manypossibilities.net
License: GPL 2.0
Date: 14-Nov-2025
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
from shapely.geometry import MultiLineString, Point, LineString
from shapely.ops import split, nearest_points, unary_union
import geopandas as gpd
import pandas as pd
import click
from libcoveofds.geojson import GeoJSONToJSONConverter, GeoJSONAssumeFeatureType
from libcoveofds.schema import OFDSSchema
from libcoveofds.jsonschemavalidate import JSONSchemaValidator
from libcoveofds.python_validate import PythonValidate
from pathlib import Path
from typing import Optional

# import matplotlib
# matplotlib.use('Qt5Agg')  # Choose an appropriate backend
# import matplotlib.pyplot as plt


def load_config(config_file):
    # Create a ConfigParser that preserves case
    class CasePreservingConfigParser(configparser.ConfigParser):
        def optionxform(self, optionstr):
            return optionstr  # Preserve original case
    
    config = CasePreservingConfigParser()
    config.read(config_file)

    # Initialize an empty dictionary to store the parsed variables
    parsed_config = {}

    # Process DEFAULT section first using defaults() method
    # This returns a dictionary of DEFAULT section values
    default_values = config.defaults()
    for option, value in default_values.items():
        parsed_config[option] = value

    # Get all other sections from the config file
    sections = config.sections()
    
    # Iterate over each section (non-DEFAULT sections override DEFAULT values)
    for section in sections:
        # Get all options (variables) within the section
        options = config.options(section)
        # Iterate over each option
        for option in options:
            # Get the value of the option
            value = config.get(section, option)
            # Assign the value to a variable with the same name
            # Non-DEFAULT sections override DEFAULT values
            parsed_config[option] = value

    return parsed_config


def process_kml_file(
    filename,
    network_id,
    network_name,
    ignore_placemarks,
    physical_infrastructure_provider_id,
    physical_infrastructure_provider_name,
    network_providers_id,
    network_providers_name,
):
    try:
        with open(filename) as f:
            kml_doc = parser.parse(f).getroot()
    except FileNotFoundError:
        print(f"\nERROR: KML file not found: {filename}")
        print(f"  Please check that the file exists and the path is correct.")
        sys.exit(1)
    except PermissionError:
        print(f"\nERROR: Permission denied when trying to read KML file: {filename}")
        print(f"  Please check file permissions.")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Failed to read or parse KML file: {filename}")
        print(f"  Error: {type(e).__name__}: {str(e)}")
        sys.exit(1)
    geojson_nodes = []
    geojson_spans = []
    # Start processing from the root Document
    # First look for Documents within the KML file.
    # Find all Documents at any level
    all_documents = list(kml_doc.iter("{http://www.opengis.net/kml/2.2}Document"))
    
    if all_documents:
        # Track processed Documents to avoid double-processing nested Documents
        processed_documents = set()
        
        # Process Documents (standard KML structure)
        # Process in order, and skip nested Documents that are children of already-processed Documents
        for document in all_documents:
            # Check if this Document is nested inside another Document
            # by checking if any of its ancestors is a Document
            is_nested_in_document = False
            parent = document.getparent()
            while parent is not None and parent != kml_doc:
                if parent.tag == "{http://www.opengis.net/kml/2.2}Document":
                    is_nested_in_document = True
                    break
                parent = parent.getparent()
            
            if is_nested_in_document:
                # This is a nested Document - it will be processed recursively by its parent
                continue
            
            document_name = document.findtext("{http://www.opengis.net/kml/2.2}name")
            print(f"Processing Document: {document_name}")
            
            processed_documents.add(document)

            nodes, spans = process_document_element(
                document,
                network_id,
                network_name,
                ignore_placemarks,
                physical_infrastructure_provider_id,
                physical_infrastructure_provider_name,
                network_providers_id,
                network_providers_name,
            )
            geojson_nodes.extend(nodes)
            geojson_spans.extend(spans)
    else:
        # No Document found, look for Folders at root level
        # Process top-level Folders (some KML files use Folders instead of Documents)
        root_folders = [child for child in kml_doc if child.tag == "{http://www.opengis.net/kml/2.2}Folder"]
        if not root_folders:
            # If no root-level Folders, check if the root itself is a Folder
            if kml_doc.tag == "{http://www.opengis.net/kml/2.2}Folder":
                root_folders = [kml_doc]
            else:
                # Look for any Folders in the document
                root_folders = list(kml_doc.iter("{http://www.opengis.net/kml/2.2}Folder"))
        
        for folder in root_folders:
            folder_name = folder.findtext("{http://www.opengis.net/kml/2.2}name")
            print(f"Processing Folder: {folder_name}")
            
            # Process this folder as if it were a Document
            nodes, spans = process_document_element(
                folder,
                network_id,
                network_name,
                ignore_placemarks,
                physical_infrastructure_provider_id,
                physical_infrastructure_provider_name,
                network_providers_id,
                network_providers_name,
            )
            geojson_nodes.extend(nodes)
            geojson_spans.extend(spans)

    print(f"Number of nodes found before deduplication: {len(geojson_nodes)}")
    geojson_nodes = remove_duplicate_nodes(geojson_nodes, 1)
    print(f"Number of nodes found after deduplication: {len(geojson_nodes)}")

    gdf_nodes = gpd.GeoDataFrame.from_features(geojson_nodes)
    gdf_spans = gpd.GeoDataFrame.from_features(geojson_spans)

    # Save initial GeoJSON objects to files as a temporary measure
    with open("output/nodes.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_nodes}, f)
    with open("output/spans.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": geojson_spans}, f)

    snapped_nodes = gdf_nodes.geometry.map(lambda point: snap_to_line(point, gdf_spans))

    # Create a new GeoDataFrame with the snapped points and geojson features
    gdf_ofds_nodes = gpd.GeoDataFrame(gdf_nodes.drop(columns="geometry").copy())
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


def process_placemark(
    placemark,
    network_id,
    network_name,
    ignore_placemarks,
    physical_infrastructure_provider_id,
    physical_infrastructure_provider_name,
    network_providers_id,
    network_providers_name,
    geojson_nodes,
    geojson_spans,
):
    """Process a single Placemark and add nodes/spans to the provided lists."""
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
                "physicalInfrastructureProvider": {
                    "id": physical_infrastructure_provider_id,
                    "name": physical_infrastructure_provider_name,
                },
                "networkProviders": [
                    {
                        "id": network_providers_id,
                        "name": network_providers_name,
                    }
                ],
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
        # Process Point elements first
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
                    "physicalInfrastructureProvider": {
                        "id": physical_infrastructure_provider_id,
                        "name": physical_infrastructure_provider_name,
                    },
                    "networkProviders": [
                        {
                            "id": network_providers_id,
                            "name": network_providers_name,
                        }
                    ],
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

        # Process LineString elements - create a separate span for each LineString
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

            # Create a separate span for each LineString
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
            shapely_line = None
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


def process_document_element(
    document,
    network_id,
    network_name,
    ignore_placemarks,
    physical_infrastructure_provider_id,
    physical_infrastructure_provider_name,
    network_providers_id,
    network_providers_name,
):
    """Process a KML Document and return a list of GeoJSON nodes and spans.

    Args:
        document (ElementTree.Element): The KML Document to process.
        network_id (str): Network ID.
        network_name (str): Network name.
        ignore_placemarks (list): List of placemark patterns to ignore.
        physical_infrastructure_provider_id (str): Physical infrastructure provider ID.
        physical_infrastructure_provider_name (str): Physical infrastructure provider name.
        network_providers_id (str): Network provider ID.
        network_providers_name (str): Network provider name.

    Returns:
        tuple: A tuple containing two lists of GeoJSON objects. The first list contains GeoJSON nodes (Points),
        and the second list contains GeoJSON spans (LineStrings).
    """
    geojson_nodes = []
    geojson_spans = []

    # Process Placemarks directly in the Document (not in Folders)
    for placemark in document.findall("{http://www.opengis.net/kml/2.2}Placemark"):
        process_placemark(
            placemark,
            network_id,
            network_name,
            ignore_placemarks,
            physical_infrastructure_provider_id,
            physical_infrastructure_provider_name,
            network_providers_id,
            network_providers_name,
            geojson_nodes,
            geojson_spans,
        )

    # Process Folders within the Document
    for folder in document.iter("{http://www.opengis.net/kml/2.2}Folder"):
        # print(f"Found folder: {folder.name.text}")

        # Process Placemarks within this Folder
        for placemark in folder.iter("{http://www.opengis.net/kml/2.2}Placemark"):
            process_placemark(
                placemark,
                network_id,
                network_name,
                ignore_placemarks,
                physical_infrastructure_provider_id,
                physical_infrastructure_provider_name,
                network_providers_id,
                network_providers_name,
                geojson_nodes,
                geojson_spans,
            )

    # Process nested Documents within this Document (recursive)
    # Note: Top-level Documents are already processed by process_kml_file,
    # but nested Documents need to be processed here
    for nested_document in document.findall("{http://www.opengis.net/kml/2.2}Document"):
        nested_document_name = nested_document.findtext("{http://www.opengis.net/kml/2.2}name")
        print(f"Processing nested Document: {nested_document_name}")
        
        nested_nodes, nested_spans = process_document_element(
            nested_document,
            network_id,
            network_name,
            ignore_placemarks,
            physical_infrastructure_provider_id,
            physical_infrastructure_provider_name,
            network_providers_id,
            network_providers_name,
        )
        geojson_nodes.extend(nested_nodes)
        geojson_spans.extend(nested_spans)

    # Return the list of GeoJSON objects
    return geojson_nodes, geojson_spans


def snap_to_line(
    point: Point, lines: gpd.GeoDataFrame, tolerance: float = 1e-4
) -> Optional[Point]:
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


def filter_ignored_nodes(gdf_nodes, ignore_placemarks):
    """
    Filter out nodes that match any pattern in ignore_placemarks.

    Args:
        gdf_nodes (GeoDataFrame): GeoDataFrame containing the node points.
        ignore_placemarks (list): List of placemark patterns to ignore.

    Returns:
        GeoDataFrame: GeoDataFrame with ignored nodes removed.
    """
    if not ignore_placemarks or len(ignore_placemarks) == 0:
        return gdf_nodes

    # Create a mask for nodes that should NOT be ignored
    mask = pd.Series([True] * len(gdf_nodes), index=gdf_nodes.index)

    for idx, row in gdf_nodes.iterrows():
        name = row.get("name", "")
        if name:
            for ignore_pattern in ignore_placemarks:
                if re.search(rf"{ignore_pattern}", name):
                    mask[idx] = False
                    break

    return gdf_nodes[mask].copy()


def extract_node_id(node_json_str):
    """Extract node ID from JSON string."""
    if node_json_str is None:
        return None
    try:
        node_dict = json.loads(node_json_str) if isinstance(node_json_str, str) else node_json_str
        return node_dict.get("id") if isinstance(node_dict, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


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
                # Check if the segment has at least 2 vertices (a valid LineString)
                if len(segment.coords) >= 2:
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
            if len(line_row.geometry.coords) >= 2:
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
    # gdf_intersects.set_crs(gdf_spans.crs, inplace=True)
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


def merge_contiguous_spans(gdf_spans, precision=6):
    """
    Merge spans that have matching endpoints (within specified precision).
    
    This function finds spans where:
    - One span's start vertex matches another span's end vertex (or vice versa)
    - Coordinates match within the specified decimal precision
    
    Iteratively merges spans until no more merges are possible (handles chains).
    
    Args:
        gdf_spans (GeoDataFrame): GeoDataFrame containing spans to merge
        precision (int): Number of decimal places for coordinate matching (default: 6)
    
    Returns:
        GeoDataFrame: GeoDataFrame with merged spans
    """
    if len(gdf_spans) == 0:
        return gdf_spans
    
    # Helper function to round coordinates to specified precision
    def round_coord(coord):
        return (round(coord[0], precision), round(coord[1], precision))
    
    # Iterate until no more merges are possible
    current_spans = gdf_spans.copy()
    max_iterations = 100  # Safety limit
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        spans_to_process = current_spans.copy()
        merged_spans = []
        processed = set()
        
        # Build endpoint map for current iteration
        endpoint_map = {}
        for idx, row in spans_to_process.iterrows():
            start_coord = round_coord(row.geometry.coords[0])
            end_coord = round_coord(row.geometry.coords[-1])
            
            if start_coord not in endpoint_map:
                endpoint_map[start_coord] = []
            endpoint_map[start_coord].append((idx, True))
            
            if end_coord not in endpoint_map:
                endpoint_map[end_coord] = []
            endpoint_map[end_coord].append((idx, False))
        
        merges_found = False
        
        # Process spans to find matches and merge
        # Continue extending each span in both directions until no more connections found
        for idx, row in spans_to_process.iterrows():
            if idx in processed:
                continue
            
            merged_span = row.copy()
            merged_coords = list(row.geometry.coords)
            
            # Keep extending this span in both directions until no more connections
            extended = True
            while extended:
                extended = False
                start_coord = round_coord(merged_coords[0])
                end_coord = round_coord(merged_coords[-1])
                
                # Try to extend at the start (find spans that end at this start)
                if start_coord in endpoint_map:
                    # Create a copy of the list to iterate over (since we'll modify it)
                    candidates = endpoint_map[start_coord][:]
                    for other_idx, is_other_start in candidates:
                        if other_idx == idx or other_idx in processed:
                            continue
                        
                        other_row = spans_to_process.loc[other_idx]
                        other_start_coord = round_coord(other_row.geometry.coords[0])
                        other_end_coord = round_coord(other_row.geometry.coords[-1])
                        
                        # Check if other span's end matches this span's start
                        if not is_other_start and other_end_coord == start_coord:
                            other_coords = list(other_row.geometry.coords)
                            # Use rounded coordinates for comparison to handle floating point precision
                            other_end_rounded = round_coord(other_coords[-1])
                            merged_start_rounded = round_coord(merged_coords[0])
                            
                            # Remove duplicate point at junction
                            if other_end_rounded == merged_start_rounded:
                                merged_coords = other_coords[:-1] + merged_coords
                            else:
                                merged_coords = other_coords + merged_coords
                            extended = True
                            processed.add(other_idx)
                            merges_found = True
                            
                            # Remove old endpoints from map and add new ones
                            old_start = other_start_coord
                            old_end = other_end_coord
                            if old_start in endpoint_map:
                                endpoint_map[old_start] = [(i, s) for i, s in endpoint_map[old_start] if i != other_idx]
                            if old_end in endpoint_map:
                                endpoint_map[old_end] = [(i, s) for i, s in endpoint_map[old_end] if i != other_idx]
                            
                            # Update endpoint map for new start coordinate
                            new_start_coord = round_coord(merged_coords[0])
                            if new_start_coord not in endpoint_map:
                                endpoint_map[new_start_coord] = []
                            break
                
                # Try to extend at the end (find spans that start at this end)
                if end_coord in endpoint_map:
                    # Create a copy of the list to iterate over (since we'll modify it)
                    candidates = endpoint_map[end_coord][:]
                    for other_idx, is_other_start in candidates:
                        if other_idx == idx or other_idx in processed:
                            continue
                        
                        other_row = spans_to_process.loc[other_idx]
                        other_start_coord = round_coord(other_row.geometry.coords[0])
                        other_end_coord = round_coord(other_row.geometry.coords[-1])
                        
                        # Check if other span's start matches this span's end
                        if is_other_start and other_start_coord == end_coord:
                            other_coords = list(other_row.geometry.coords)
                            # Use rounded coordinates for comparison to handle floating point precision
                            merged_end_rounded = round_coord(merged_coords[-1])
                            other_start_rounded = round_coord(other_coords[0])
                            
                            # Remove duplicate point at junction
                            if merged_end_rounded == other_start_rounded:
                                merged_coords = merged_coords + other_coords[1:]
                            else:
                                merged_coords = merged_coords + other_coords
                            extended = True
                            processed.add(other_idx)
                            merges_found = True
                            
                            # Remove old endpoints from map and add new ones
                            old_start = other_start_coord
                            old_end = other_end_coord
                            if old_start in endpoint_map:
                                endpoint_map[old_start] = [(i, s) for i, s in endpoint_map[old_start] if i != other_idx]
                            if old_end in endpoint_map:
                                endpoint_map[old_end] = [(i, s) for i, s in endpoint_map[old_end] if i != other_idx]
                            
                            # Update endpoint map for new end coordinate
                            new_end_coord = round_coord(merged_coords[-1])
                            if new_end_coord not in endpoint_map:
                                endpoint_map[new_end_coord] = []
                            break
            
            # Update geometry
            if len(merged_coords) >= 2:
                merged_span["geometry"] = LineString(merged_coords)
            
            merged_spans.append(merged_span)
            processed.add(idx)
        
        # If no merges found, we're done
        if not merges_found:
            break
        
        # Create new GeoDataFrame for next iteration
        current_spans = gpd.GeoDataFrame(merged_spans, crs=spans_to_process.crs)
    
    return current_spans


def add_missing_nodes(
    gdf_spans,
    gdf_nodes,
    network_id,
    network_name,
    network_links,
    physical_infrastructure_provider_id,
    physical_infrastructure_provider_name,
    network_providers_id,
    network_providers_name,
    tolerance=1e-3,  # Increased to match find_end_point tolerance
):
    # Ensure that each segment has a start and end node
    # If not, add the missing nodes to the ofds_points_gdf
    new_nodes = []  # Store new nodes to be appended to the ofds_points_gdf
    new_nodes_geoms = []  # Store geometries for proximity checking
    
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
            start_point_geom = Point(start_point)
            # Check if we've already created a node nearby (within tolerance)
            nearby_exists = False
            for existing_geom in new_nodes_geoms:
                if start_point_geom.distance(existing_geom) <= tolerance:
                    nearby_exists = True
                    break
            
            if not nearby_exists:
                new_node = append_node(
                    start_point,
                    network_id,
                    network_name,
                    network_links,
                    physical_infrastructure_provider_id,
                    physical_infrastructure_provider_name,
                    network_providers_id,
                    network_providers_name,
                )
                new_nodes.append(new_node)
                new_nodes_geoms.append(start_point_geom)
                
        if not end_exists:
            end_point_geom = Point(end_point)
            # Check if we've already created a node nearby (within tolerance)
            nearby_exists = False
            for existing_geom in new_nodes_geoms:
                if end_point_geom.distance(existing_geom) <= tolerance:
                    nearby_exists = True
                    break
            
            if not nearby_exists:
                new_node = append_node(
                    end_point,
                    network_id,
                    network_name,
                    network_links,
                    physical_infrastructure_provider_id,
                    physical_infrastructure_provider_name,
                    network_providers_id,
                    network_providers_name,
                )
                new_nodes.append(new_node)
                new_nodes_geoms.append(end_point_geom)

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
        new_nodes_gdf = gpd.GeoDataFrame(
            {col: pd.Series(dtype=gdf_nodes[col].dtype) for col in gdf_nodes.columns}
        )
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


def write_debug_geojson(gdf_nodes, gdf_spans, output_dir, phase_name, output_prefix):
    """
    Write debug GeoJSON files for nodes and spans after a phase.
    
    Args:
        gdf_nodes (GeoDataFrame): GeoDataFrame containing nodes.
        gdf_spans (GeoDataFrame): GeoDataFrame containing spans.
        output_dir (str): Output directory path.
        phase_name (str): Name of the phase (e.g., "phase3", "phase4").
        output_prefix (str): Prefix for output filenames.
    """
    from pathlib import Path
    
    # Check if output_dir is provided
    if output_dir is None:
        print(f"  [DEBUG] Warning: debug_output_dir is None, skipping debug file write for {phase_name}")
        return
    
    # Ensure output directory exists
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Set CRS if not already set
    if gdf_nodes.crs is None:
        gdf_nodes.set_crs("EPSG:4326", inplace=True)
    if gdf_spans.crs is None:
        gdf_spans.set_crs("EPSG:4326", inplace=True)
    
    # Create filenames
    nodes_file = output_path / f"{output_prefix}_debug-{phase_name}_nodes.geojson"
    spans_file = output_path / f"{output_prefix}_debug-{phase_name}_spans.geojson"
    
    # Write GeoJSON files
    gdf_nodes.to_file(nodes_file, driver="GeoJSON")
    gdf_spans.to_file(spans_file, driver="GeoJSON")
    
    print(f"  [DEBUG] Wrote debug files: {nodes_file.name}, {spans_file.name}")


def _extract_id(x):
    """Helper function to extract ID from node reference (dict, JSON string, or other)."""
    if isinstance(x, dict):
        return x.get("id")
    elif isinstance(x, str):
        try:
            return json.loads(x).get("id")
        except json.JSONDecodeError:
            return x
    return x


def _setup_consolidation(gdf_ofds_nodes, gdf_ofds_spans, threshold_meters):
    """
    Phase 1: Setup and initial filtering.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes
        gdf_ofds_spans: GeoDataFrame containing spans
        threshold_meters: Distance threshold in meters
        
    Returns:
        tuple: (threshold, auto_gen_nodes, other_nodes, span_endpoint_ids) or None if no auto-generated nodes
    """
    # Convert threshold from meters to degrees
    # 1 degree ≈ 111 km = 111,000 meters
    METERS_TO_DEGREES = 1.0 / 111000.0
    threshold = threshold_meters * METERS_TO_DEGREES

    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]

    if len(auto_gen_nodes) == 0:
        print("No auto-generated nodes to process.")
        return None

    other_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] != "Auto generated missing node"
    ]

    start_ids = gdf_ofds_spans["start"].apply(_extract_id)
    end_ids = gdf_ofds_spans["end"].apply(_extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())

    return threshold, auto_gen_nodes, other_nodes, span_endpoint_ids


def _analyze_auto_generated_nodes(auto_gen_nodes, other_nodes, gdf_ofds_spans, span_endpoint_ids):
    """
    Phase 2: Analyze auto-generated nodes and print distances to nearest nodes and spans.
    
    Args:
        auto_gen_nodes: GeoDataFrame of auto-generated nodes
        other_nodes: GeoDataFrame of other (proper) nodes
        gdf_ofds_spans: GeoDataFrame containing spans
        span_endpoint_ids: Set of node IDs that are span endpoints
    """
    print(f"\nAnalyzing {len(auto_gen_nodes)} auto-generated nodes:")
    print("-" * 80)

    for node_idx, node_row in auto_gen_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        is_endpoint = node_id in span_endpoint_ids

        min_node_distance = float("inf")
        nearest_node_id = None

        if len(other_nodes) > 0:
            for other_idx, other_row in other_nodes.iterrows():
                other_point = other_row.geometry
                distance = node_point.distance(other_point)
                if distance < min_node_distance:
                    min_node_distance = distance
                    nearest_node_id = other_row["id"]

        min_span_distance = float("inf")
        nearest_span_id = None
        nearest_point_on_span = None

        for span_idx, span_row in gdf_ofds_spans.iterrows():
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_id = span_row.get("id", f"span_{span_idx}")
                nearest_point_on_span = nearest_point

        status = "endpoint" if is_endpoint else "isolated"


def _merge_auto_generated_with_proper_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    """
    Phase 3: Merge auto-generated nodes that are close to proper nodes.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes (modified in place)
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        threshold: Distance threshold in degrees
        
    Returns:
        tuple: (gdf_ofds_nodes, gdf_ofds_spans) - Updated GeoDataFrames
    """
    # Find all clusters where auto-generated nodes are within threshold of proper nodes
    coordinates = np.array([(point.x, point.y) for point in gdf_ofds_nodes.geometry])
    tree = KDTree(coordinates)
    clusters = [
        indices
        for indices in tree.query_radius(coordinates, r=threshold)
        if len(indices) > 1
    ]

    # Build a mapping: auto_generated_node_id -> proper_node_id
    # This ensures ALL auto-generated nodes within threshold get merged
    auto_to_proper_mapping = {}
    
    for cluster in clusters:
        # Separate auto-generated nodes from proper nodes in this cluster
        auto_gen_indices = []
        proper_indices = []
        
        for idx in cluster:
            node_name = gdf_ofds_nodes.iloc[idx]["name"]
            if node_name == "Auto generated missing node":
                auto_gen_indices.append(idx)
            else:
                proper_indices.append(idx)
        
        # If we have auto-generated nodes and at least one proper node, merge them
        if auto_gen_indices and proper_indices:
            # Use the first proper node as the target (all auto-generated nodes merge to it)
            proper_node_idx = proper_indices[0]
            proper_node_id = gdf_ofds_nodes.iloc[proper_node_idx]["id"]
            
            # Map ALL auto-generated nodes in this cluster to the proper node
            for auto_idx in auto_gen_indices:
                auto_node_id = gdf_ofds_nodes.iloc[auto_idx]["id"]
                auto_to_proper_mapping[auto_node_id] = proper_node_id

    # Update all spans that reference auto-generated nodes to reference proper nodes instead
    merged_node_ids = []
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_updated = False
        end_updated = False
        
        # Check and update start node
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
            if start_id in auto_to_proper_mapping:
                proper_node_id = auto_to_proper_mapping[start_id]
                # Find the proper node
                proper_node_row = gdf_ofds_nodes[gdf_ofds_nodes["id"] == proper_node_id]
                if not proper_node_row.empty:
                    proper_node = proper_node_row.iloc[0]
                    proper_node_geometry = proper_node["geometry"]
                    
                    # Update the node ID, name, and location
                    start_dict["id"] = proper_node["id"]
                    start_dict["name"] = proper_node["name"]
                    if "location" in start_dict:
                        start_dict["location"]["coordinates"] = [
                            proper_node_geometry.x,
                            proper_node_geometry.y,
                        ]
                    
                    merged_node_ids.append(start_id)
                    start_updated = True
                    
                    # Update the span geometry endpoint to match the proper node
                    span_geometry = span["geometry"]
                    updated_coords = list(span_geometry.coords)
                    updated_coords[0] = (proper_node_geometry.x, proper_node_geometry.y)
                    span_geometry = LineString(updated_coords)
                    gdf_ofds_spans.at[index, "geometry"] = span_geometry
                else:
                    # Proper node not found - this shouldn't happen but log it
                    span_name = span.get('name', 'unknown')
                    print(
                        f"WARNING: Proper node ID '{proper_node_id}' not found for "
                        f"auto-generated node '{start_id}' in span '{span_name}'. "
                        f"Span will retain reference to auto-generated node."
                    )
        
        # Check and update end node independently
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
            if end_id in auto_to_proper_mapping:
                proper_node_id = auto_to_proper_mapping[end_id]
                # Find the proper node
                proper_node_row = gdf_ofds_nodes[gdf_ofds_nodes["id"] == proper_node_id]
                if not proper_node_row.empty:
                    proper_node = proper_node_row.iloc[0]
                    proper_node_geometry = proper_node["geometry"]
                    
                    # Update the node ID, name, and location
                    end_dict["id"] = proper_node["id"]
                    end_dict["name"] = proper_node["name"]
                    if "location" in end_dict:
                        end_dict["location"]["coordinates"] = [
                            proper_node_geometry.x,
                            proper_node_geometry.y,
                        ]
                    
                    merged_node_ids.append(end_id)
                    end_updated = True
                    
                    # Update the span geometry endpoint to match the proper node
                    span_geometry = span["geometry"]
                    updated_coords = list(span_geometry.coords)
                    updated_coords[-1] = (proper_node_geometry.x, proper_node_geometry.y)
                    span_geometry = LineString(updated_coords)
                    gdf_ofds_spans.at[index, "geometry"] = span_geometry
                else:
                    # Proper node not found - this shouldn't happen but log it
                    span_name = span.get('name', 'unknown')
                    print(
                        f"WARNING: Proper node ID '{proper_node_id}' not found for "
                        f"auto-generated node '{end_id}' in span '{span_name}'. "
                        f"Span will retain reference to auto-generated node."
                    )
        
        # Update span endpoints if they were modified
        if start_updated or end_updated:
            start_json = json.dumps(convert_to_serializable(start_dict))
            end_json = json.dumps(convert_to_serializable(end_dict))
            gdf_ofds_spans.at[index, "start"] = start_json
            gdf_ofds_spans.at[index, "end"] = end_json

    # Remove all merged auto-generated nodes
    gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(merged_node_ids)]
    print(
        f"Phase 3: Merged {len(set(merged_node_ids))} auto-generated nodes "
        f"with proper nodes. Remaining nodes: {len(gdf_ofds_nodes)}"
    )
    
    return gdf_ofds_nodes, gdf_ofds_spans


def _merge_large_auto_generated_clusters(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    """
    Phase 4: Merge clusters of 3+ auto-generated nodes into network fork nodes.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes (modified in place)
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        threshold: Distance threshold in degrees
        
    Returns:
        tuple: (gdf_ofds_nodes, gdf_ofds_spans) - Updated GeoDataFrames
    """
    # Re-filter auto-generated nodes after Phase 3 merges
    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]
    filtered_nodes = auto_gen_nodes.copy()
    coordinates = np.array([(point.x, point.y) for point in filtered_nodes.geometry])
    
    nodes_to_remove_phase4 = set()
    new_fork_nodes = []
    
    if len(coordinates) > 0:
        tree = KDTree(coordinates)
        # Find all clusters of nodes within threshold
        all_clusters = [
            indices
            for indices in tree.query_radius(coordinates, r=threshold)
            if len(indices) >= 2  # Process clusters with 2+ nodes
        ]
        
        # Separate clusters into pairs (2 nodes) and larger clusters (3+ nodes)
        pair_clusters = [c for c in all_clusters if len(c) == 2]
        larger_clusters = [c for c in all_clusters if len(c) >= 3]
        
        # Remove duplicate clusters (same nodes, different order)
        def normalize_cluster(cluster):
            return tuple(sorted(cluster))
        
        unique_larger_clusters = list(set([normalize_cluster(c) for c in larger_clusters]))

        # Process larger clusters (3+ nodes):
        # Merge them into a single "network fork" node
        # All spans connected to those nodes terminate at the "network fork" node
        for cluster in unique_larger_clusters:
            # Get all node IDs in this cluster
            cluster_node_ids = [filtered_nodes.iloc[idx]["id"] for idx in cluster]
            cluster_node_indices = list(cluster)
            
            # Calculate centroid of all nodes in cluster
            cluster_points = [filtered_nodes.iloc[idx].geometry for idx in cluster]
            centroid_x = sum(p.x for p in cluster_points) / len(cluster_points)
            centroid_y = sum(p.y for p in cluster_points) / len(cluster_points)
            fork_location = Point(centroid_x, centroid_y)
            
            # Get network info from first node in cluster
            first_node_row = filtered_nodes.iloc[cluster_node_indices[0]]
            network_info = first_node_row.get("network", {})
            physical_infrastructure_provider = first_node_row.get("physicalInfrastructureProvider", {})
            network_providers = first_node_row.get("networkProviders", [])
            
            # Create new "network fork" node
            fork_node_id = str(uuid.uuid4())
            fork_node = {
                "id": fork_node_id,
                "name": "network fork",
                "geometry": fork_location,
                "network": network_info,
                "physicalInfrastructureProvider": physical_infrastructure_provider,
                "networkProviders": network_providers,
                "featureType": "node",
            }
            new_fork_nodes.append(fork_node)
            
            # Find all spans connected to any node in the cluster
            connected_spans = []
            for span_idx, span_row in gdf_ofds_spans.iterrows():
                start_dict = (
                    json.loads(span_row["start"])
                    if isinstance(span_row["start"], str) and span_row["start"] is not None
                    else span_row["start"]
                )
                end_dict = (
                    json.loads(span_row["end"])
                    if isinstance(span_row["end"], str) and span_row["end"] is not None
                    else span_row["end"]
                )
                
                start_id = start_dict.get("id") if isinstance(start_dict, dict) else None
                end_id = end_dict.get("id") if isinstance(end_dict, dict) else None
                
                # Get node names to check if endpoints are proper nodes
                start_name = start_dict.get("name") if isinstance(start_dict, dict) else None
                end_name = end_dict.get("name") if isinstance(end_dict, dict) else None
                
                # Skip spans that have proper nodes as endpoints (already merged in Phase 3)
                # Phase 4 should only process spans between auto-generated nodes
                if (start_name and start_name != "Auto generated missing node" and start_name != "network fork") or \
                   (end_name and end_name != "Auto generated missing node" and end_name != "network fork"):
                    # This span has a proper node endpoint - skip it
                    continue
                
                # Check if span is connected to any node in cluster
                if start_id in cluster_node_ids or end_id in cluster_node_ids:
                    connected_spans.append((
                        span_idx, span_row,
                        start_id in cluster_node_ids,
                        end_id in cluster_node_ids
                    ))
            
            # Update all connected spans to point to fork node
            fork_node_info = {
                "id": fork_node_id,
                "name": "network fork",
                "location": {
                    "type": "Point",
                    "coordinates": [centroid_x, centroid_y],
                },
            }
            
            for span_idx, span_row, start_in_cluster, end_in_cluster in connected_spans:
                start_dict = (
                    json.loads(span_row["start"])
                    if isinstance(span_row["start"], str) and span_row["start"] is not None
                    else span_row["start"]
                )
                end_dict = (
                    json.loads(span_row["end"])
                    if isinstance(span_row["end"], str) and span_row["end"] is not None
                    else span_row["end"]
                )
                
                span_geometry = span_row["geometry"]
                updated_coords = list(span_geometry.coords)
                updated = False
                
                # Update start endpoint if it's in cluster
                if start_in_cluster and isinstance(start_dict, dict):
                    start_dict["id"] = fork_node_id
                    start_dict["name"] = "network fork"
                    if "location" in start_dict:
                        start_dict["location"]["coordinates"] = [centroid_x, centroid_y]
                    # Extend geometry to fork location
                    updated_coords[0] = (centroid_x, centroid_y)
                    updated = True
                
                # Update end endpoint if it's in cluster
                if end_in_cluster and isinstance(end_dict, dict):
                    end_dict["id"] = fork_node_id
                    end_dict["name"] = "network fork"
                    if "location" in end_dict:
                        end_dict["location"]["coordinates"] = [centroid_x, centroid_y]
                    # Extend geometry to fork location
                    updated_coords[-1] = (centroid_x, centroid_y)
                    updated = True
                
                if updated:
                    # Remove duplicate consecutive coordinates
                    cleaned_coords = [updated_coords[0]]
                    for coord in updated_coords[1:]:
                        if coord != cleaned_coords[-1]:
                            cleaned_coords.append(coord)
                    
                    # Ensure we have at least 2 coordinates for a valid LineString
                    if len(cleaned_coords) >= 2:
                        span_geometry = LineString(cleaned_coords)
                        gdf_ofds_spans.at[span_idx, "geometry"] = span_geometry
                        gdf_ofds_spans.at[span_idx, "start"] = json.dumps(convert_to_serializable(start_dict))
                        gdf_ofds_spans.at[span_idx, "end"] = json.dumps(convert_to_serializable(end_dict))
                    else:
                        # Skip spans that would have invalid geometry (only 1 point)
                        print(f"Warning: Skipping span {span_idx} - insufficient coordinates after cleaning ({len(cleaned_coords)} point(s))")
            
            # Mark all nodes in cluster for removal
            for node_id in cluster_node_ids:
                nodes_to_remove_phase4.add(node_id)
        
        # Add new fork nodes to the nodes GeoDataFrame
        if new_fork_nodes:
            fork_nodes_gdf = gpd.GeoDataFrame(new_fork_nodes, crs=gdf_ofds_nodes.crs)
            gdf_ofds_nodes = pd.concat([gdf_ofds_nodes, fork_nodes_gdf], ignore_index=True)

        # Remove the nodes processed in Phase 4
        if nodes_to_remove_phase4:
            gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(list(nodes_to_remove_phase4))]
            clusters_processed = len(unique_larger_clusters)
            fork_nodes_created = len(new_fork_nodes)
            print(
                f"Phase 4: Processed {clusters_processed} clusters (3+ nodes). "
                f"Removed {len(nodes_to_remove_phase4)} auto-generated nodes, "
                f"created {fork_nodes_created} network fork nodes. "
                f"Remaining nodes: {len(gdf_ofds_nodes)}"
            )
    
    return gdf_ofds_nodes, gdf_ofds_spans


def _merge_pair_auto_generated_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    """
    Phase 5: Merge pairs of auto-generated nodes by joining their connected spans.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes (modified in place)
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        threshold: Distance threshold in degrees
        
    Returns:
        tuple: (gdf_ofds_nodes, gdf_ofds_spans) - Updated GeoDataFrames
    """
    # Re-filter auto-generated nodes after Phase 4 merges
    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]
    filtered_nodes = auto_gen_nodes.copy()
    coordinates = np.array([(point.x, point.y) for point in filtered_nodes.geometry])
    
    nodes_to_remove_phase45 = set()
    spans_to_remove = []
    new_spans = []
    
    if len(coordinates) > 0:
        tree = KDTree(coordinates)
        # Find all clusters of nodes within threshold
        all_clusters = [
            indices
            for indices in tree.query_radius(coordinates, r=threshold)
            if len(indices) >= 2  # Process clusters with 2+ nodes
        ]
        
        # Get only pairs (2 nodes)
        pair_clusters = [c for c in all_clusters if len(c) == 2]
        
        # Remove duplicate clusters (same nodes, different order)
        def normalize_cluster(cluster):
            return tuple(sorted(cluster))
        
        unique_pair_clusters = list(set([normalize_cluster(c) for c in pair_clusters]))

        # Convert pair clusters to pairs (each cluster of 2 nodes becomes one pair)
        unique_pairs = [
            (min(cluster[0], cluster[1]), max(cluster[0], cluster[1]))
            for cluster in unique_pair_clusters
        ]
        # Remove duplicate pairs
        unique_pairs = list(set(unique_pairs))

        for pair in unique_pairs:
            node_a_idx = pair[0]
            node_b_idx = pair[1]
            node_a_id = filtered_nodes.iloc[node_a_idx]["id"]
            node_b_id = filtered_nodes.iloc[node_b_idx]["id"]

            # Find all spans connected to Node A
            spans_connected_to_a = []
            spans_connected_to_b = []

            for span_idx, span_row in gdf_ofds_spans.iterrows():
                try:
                    start_dict = (
                        json.loads(span_row["start"])
                        if isinstance(span_row["start"], str) and span_row["start"] is not None
                        else span_row["start"]
                    )
                    end_dict = (
                        json.loads(span_row["end"])
                        if isinstance(span_row["end"], str) and span_row["end"] is not None
                        else span_row["end"]
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"WARNING: Failed to parse span {span_idx} start/end JSON: {e}. Skipping.")
                    continue
                
                start_id = start_dict.get("id") if isinstance(start_dict, dict) else None
                end_id = end_dict.get("id") if isinstance(end_dict, dict) else None
                
                # Check if span is connected to Node A or Node B
                connected_to_a = (start_id == node_a_id or end_id == node_a_id)
                connected_to_b = (start_id == node_b_id or end_id == node_b_id)
                
                # Only process spans that are connected to at least one of our target nodes
                if not (connected_to_a or connected_to_b):
                    continue

                # Check if span is connected to Node A
                if connected_to_a:
                    spans_connected_to_a.append((span_idx, span_row, start_id == node_a_id))

                # Check if span is connected to Node B
                if connected_to_b:
                    spans_connected_to_b.append((span_idx, span_row, start_id == node_b_id))

            # Join spans: combine all spans connected to Node A and Node B
            # Deduplicate spans (a span connecting A to B will appear in both lists)
            unique_connected_spans = {}
            for span_idx, span_row, is_start_a in spans_connected_to_a:
                if span_idx not in unique_connected_spans:
                    unique_connected_spans[span_idx] = (span_row, is_start_a, False)
            for span_idx, span_row, is_start_b in spans_connected_to_b:
                if span_idx not in unique_connected_spans:
                    unique_connected_spans[span_idx] = (span_row, False, is_start_b)
                else:
                    # Span connects both nodes - mark it
                    unique_connected_spans[span_idx] = (span_row, True, True)

            # When exactly 2 auto-generated nodes are in proximity:
            # If exactly 2 spans are connected, merge them into a single span and remove both nodes
            if len(unique_connected_spans) == 2:
                # Exactly 2 spans: merge them into a single span
                # First, collect span information
                span_list = list(unique_connected_spans.items())
                span1_idx, (span1_row, connects_to_a1, connects_to_b1) = span_list[0]
                span2_idx, (span2_row, connects_to_a2, connects_to_b2) = span_list[1]
                
                span1_geom = span1_row["geometry"]
                span2_geom = span2_row["geometry"]
                
                span1_start_dict = (
                    json.loads(span1_row["start"])
                    if isinstance(span1_row["start"], str) and span1_row["start"] is not None
                    else span1_row["start"]
                )
                span1_end_dict = (
                    json.loads(span1_row["end"])
                    if isinstance(span1_row["end"], str) and span1_row["end"] is not None
                    else span1_row["end"]
                )
                span2_start_dict = (
                    json.loads(span2_row["start"])
                    if isinstance(span2_row["start"], str) and span2_row["start"] is not None
                    else span2_row["start"]
                )
                span2_end_dict = (
                    json.loads(span2_row["end"])
                    if isinstance(span2_row["end"], str) and span2_row["end"] is not None
                    else span2_row["end"]
                )
                
                # Identify which endpoints are connected to the auto-generated nodes
                span1_start_id = span1_start_dict.get("id") if isinstance(span1_start_dict, dict) else None
                span1_end_id = span1_end_dict.get("id") if isinstance(span1_end_dict, dict) else None
                span2_start_id = span2_start_dict.get("id") if isinstance(span2_start_dict, dict) else None
                span2_end_id = span2_end_dict.get("id") if isinstance(span2_end_dict, dict) else None
                
                # Determine which endpoint of span1 is connected to Node A or B
                span1_connected_to_auto = None
                span1_other_end = None
                if span1_start_id == node_a_id or span1_start_id == node_b_id:
                    span1_connected_to_auto = "start"
                    span1_other_end = span1_end_dict
                elif span1_end_id == node_a_id or span1_end_id == node_b_id:
                    span1_connected_to_auto = "end"
                    span1_other_end = span1_start_dict
                
                # Determine which endpoint of span2 is connected to Node A or B
                span2_connected_to_auto = None
                span2_other_end = None
                if span2_start_id == node_a_id or span2_start_id == node_b_id:
                    span2_connected_to_auto = "start"
                    span2_other_end = span2_end_dict
                elif span2_end_id == node_a_id or span2_end_id == node_b_id:
                    span2_connected_to_auto = "end"
                    span2_other_end = span2_start_dict
                
                # Orient spans so the auto-generated node endpoints are connected
                # The merged span should go from span1's other end to span2's other end
                # We need to connect the auto-generated node endpoints together
                if span1_connected_to_auto == "start" and span2_connected_to_auto == "start":
                    # Both connected at start: span1 = A->X, span2 = B->Y
                    # Reverse span1 to get X->A, then join with B->Y to get X->A->B->Y
                    geom1 = LineString(list(span1_geom.coords)[::-1])
                    geom2 = span2_geom
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "start" and span2_connected_to_auto == "end":
                    # Span1 at start (A->X), span2 at end (Y->B)
                    # Join A->X with Y->B to get A->X->Y->B, then reverse to get X->Y->B->A
                    # Actually, we want X->Y, so reverse span1: X->A, reverse span2: B->Y
                    # Join: X->A + B->Y = X->A->B->Y, which becomes X->Y
                    geom1 = LineString(list(span1_geom.coords)[::-1])
                    geom2 = LineString(list(span2_geom.coords)[::-1])
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "end" and span2_connected_to_auto == "start":
                    # Span1 at end (X->A), span2 at start (B->Y)
                    # Join X->A with B->Y to get X->A->B->Y, which becomes X->Y
                    geom1 = span1_geom
                    geom2 = span2_geom
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "end" and span2_connected_to_auto == "end":
                    # Both connected at end: span1 = X->A, span2 = Y->B
                    # Reverse span2 to get B->Y, then join with X->A to get X->A->B->Y
                    geom1 = span1_geom
                    geom2 = LineString(list(span2_geom.coords)[::-1])
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                else:
                    # Fallback: use closest ends (shouldn't happen if spans are connected to nodes)
                    span1_start_coord = span1_geom.coords[0]
                    span1_end_coord = span1_geom.coords[-1]
                    span2_start_coord = span2_geom.coords[0]
                    span2_end_coord = span2_geom.coords[-1]
                    
                    span1_start_point = Point(span1_start_coord)
                    span1_end_point = Point(span1_end_coord)
                    span2_start_point = Point(span2_start_coord)
                    span2_end_point = Point(span2_end_coord)
                    
                    dist_start1_end2 = span1_start_point.distance(span2_end_point)
                    dist_end1_start2 = span1_end_point.distance(span2_start_point)
                    
                    if dist_start1_end2 <= dist_end1_start2:
                        geom1 = span1_geom
                        geom2 = LineString(list(span2_geom.coords)[::-1])
                        new_start_node = span1_start_dict
                        new_end_node = span2_start_dict
                    else:
                        geom1 = LineString(list(span1_geom.coords)[::-1])
                        geom2 = span2_geom
                        new_start_node = span1_end_dict
                        new_end_node = span2_end_dict
                
                # Join the geometries
                joined_coords = list(geom1.coords) + list(geom2.coords)
                
                # Remove duplicate consecutive coordinates
                cleaned_coords = [joined_coords[0]]
                for coord in joined_coords[1:]:
                    if coord != cleaned_coords[-1]:
                        cleaned_coords.append(coord)
                
                if len(cleaned_coords) >= 2:
                    # Validate that the node references exist in gdf_ofds_nodes
                    valid_start_node = None
                    valid_end_node = None
                    
                    if new_start_node and isinstance(new_start_node, dict):
                        start_node_id = new_start_node.get("id")
                        if start_node_id:
                            # Check if node exists in gdf_ofds_nodes
                            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == start_node_id].empty
                            if node_exists:
                                valid_start_node = new_start_node
                            else:
                                print(
                                    f"WARNING: Phase 5 - Start node ID '{start_node_id}' "
                                    f"not found in nodes GeoDataFrame. Skipping span merge."
                                )
                                continue
                    
                    if new_end_node and isinstance(new_end_node, dict):
                        end_node_id = new_end_node.get("id")
                        if end_node_id:
                            # Check if node exists in gdf_ofds_nodes
                            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == end_node_id].empty
                            if node_exists:
                                valid_end_node = new_end_node
                            else:
                                print(
                                    f"WARNING: Phase 5 - End node ID '{end_node_id}' "
                                    f"not found in nodes GeoDataFrame. Skipping span merge."
                                )
                                continue
                    
                    # Only create the new span if we have valid node references
                    if valid_start_node is None and valid_end_node is None:
                        print(
                            f"WARNING: Phase 5 - Both start and end nodes are invalid. "
                            f"Skipping span merge."
                        )
                        continue
                    
                    joined_geometry = LineString(cleaned_coords)
                    
                    # Create new span
                    new_span = span1_row.copy()
                    new_span["id"] = str(uuid.uuid4())
                    new_span["geometry"] = joined_geometry
                    if valid_start_node:
                        new_span["start"] = json.dumps(
                            convert_to_serializable(valid_start_node)
                        )
                    else:
                        new_span["start"] = None
                    if valid_end_node:
                        new_span["end"] = json.dumps(
                            convert_to_serializable(valid_end_node)
                        )
                    else:
                        new_span["end"] = None
                    
                    new_spans.append(new_span)
                
                # Mark both spans for removal
                if span1_idx not in spans_to_remove:
                    spans_to_remove.append(span1_idx)
                if span2_idx not in spans_to_remove:
                    spans_to_remove.append(span2_idx)

                # Mark both nodes for removal
                nodes_to_remove_phase45.add(node_a_id)
                nodes_to_remove_phase45.add(node_b_id)

        # Remove old spans and add new joined spans
        if spans_to_remove:
            gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove)
            if new_spans:
                new_spans_gdf = gpd.GeoDataFrame(new_spans, crs=gdf_ofds_spans.crs)
                gdf_ofds_spans = pd.concat(
                    [gdf_ofds_spans, new_spans_gdf], ignore_index=True
                )

        # Remove the nodes processed in Phase 5
        if nodes_to_remove_phase45:
            gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(list(nodes_to_remove_phase45))]
            pairs_processed = len(unique_pairs)
            print(
                f"Phase 5: Processed {pairs_processed} pairs (2 nodes). "
                f"Removed {len(nodes_to_remove_phase45)} auto-generated nodes, "
                f"joined {len(spans_to_remove)} spans into {len(new_spans)} spans. "
                f"Remaining nodes: {len(gdf_ofds_nodes)}"
            )
    
    return gdf_ofds_nodes, gdf_ofds_spans


def _split_spans_at_auto_generated_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold, threshold_meters, debug_enabled, debug_output_dir, debug_output_prefix):
    """
    Phase 6: Split spans at auto-generated nodes to create fork points.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes (modified in place)
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        threshold: Distance threshold in degrees
        threshold_meters: Distance threshold in meters (for reporting)
        debug_enabled: Whether to write debug files
        debug_output_dir: Directory for debug files
        debug_output_prefix: Prefix for debug filenames
        
    Returns:
        tuple: (gdf_ofds_nodes, gdf_ofds_spans) - Updated GeoDataFrames
    """
    METERS_TO_DEGREES = 1.0 / 111000.0
    
    # Get all auto-generated nodes that are endpoints of spans
    start_ids = gdf_ofds_spans["start"].apply(_extract_id)
    end_ids = gdf_ofds_spans["end"].apply(_extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())
    
    auto_gen_endpoint_nodes = gdf_ofds_nodes[
        (gdf_ofds_nodes["name"] == "Auto generated missing node") &
        (gdf_ofds_nodes["id"].isin(span_endpoint_ids))
    ]
    
    spans_to_remove = []
    new_spans = []
    nodes_to_rename = {}
    node_processing_status = {}
    
    for node_idx, node_row in auto_gen_endpoint_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        
        # Find the span where this node is an endpoint
        node_span_idx = None
        node_is_start = False
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove:
                continue
            span_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            span_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )
            if isinstance(span_start, dict) and span_start.get("id") == node_id:
                node_span_idx = span_idx
                node_is_start = True
                break
            elif isinstance(span_end, dict) and span_end.get("id") == node_id:
                node_span_idx = span_idx
                node_is_start = False
                break
        
        if node_span_idx is None:
            node_processing_status[node_id] = "SKIPPED: Not found as endpoint of any span"
            continue
        
        # Find nearest span (excluding the span where this node is an endpoint)
        min_span_distance = float("inf")
        nearest_span_idx = None
        nearest_point_on_span = None
        
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove or span_idx == node_span_idx:
                continue
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_idx = span_idx
                nearest_point_on_span = nearest_point
        
        # Check if within threshold
        if min_span_distance == float("inf") or min_span_distance > threshold:
            dist_str = f"{(min_span_distance / METERS_TO_DEGREES):.2f}" if min_span_distance != float("inf") else "inf"
            node_processing_status[node_id] = f"SKIPPED: Too far ({dist_str}m > {threshold_meters}m)"
            continue
        
        if nearest_span_idx is None or nearest_point_on_span is None:
            node_processing_status[node_id] = "SKIPPED: Could not find nearest span"
            continue
        
        # Check if nearest point is at an endpoint of the span (skip if so)
        target_span_row = gdf_ofds_spans.loc[nearest_span_idx]
        target_span_line = target_span_row.geometry
        target_start_point = Point(target_span_line.coords[0])
        target_end_point = Point(target_span_line.coords[-1])
        endpoint_tolerance = 1e-6
        
        if (
            nearest_point_on_span.distance(target_start_point) < endpoint_tolerance
            or nearest_point_on_span.distance(target_end_point) < endpoint_tolerance
        ):
            node_processing_status[node_id] = "SKIPPED: Nearest point is at span endpoint"
            continue
        
        # Process: extend node's span, split target span, create fork
        # 1. Extend the span that has the auto-generated node to the fork point
        node_span_row = gdf_ofds_spans.loc[node_span_idx]
        node_span_geom = node_span_row.geometry
        node_span_start = (
            json.loads(node_span_row["start"])
            if isinstance(node_span_row["start"], str)
            else node_span_row["start"]
        )
        node_span_end = (
            json.loads(node_span_row["end"])
            if isinstance(node_span_row["end"], str)
            else node_span_row["end"]
        )
        
        # Create fork node info
        fork_node_info = {
            "id": node_id,
            "name": "network fork",
            "location": {
                "type": "Point",
                "coordinates": [nearest_point_on_span.x, nearest_point_on_span.y],
            },
        }
        
        # Extend the node's span to the fork point
        node_span_coords = list(node_span_geom.coords)
        fork_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
        
        if node_is_start:
            # Node is at start, extend from start
            if Point(node_span_coords[0]).distance(nearest_point_on_span) > 1e-9:
                node_span_coords.insert(0, fork_coord)
            extended_node_span_start = fork_node_info
            extended_node_span_end = node_span_end.copy() if isinstance(node_span_end, dict) else node_span_end
        else:
            # Node is at end, extend from end
            if Point(node_span_coords[-1]).distance(nearest_point_on_span) > 1e-9:
                node_span_coords.append(fork_coord)
            extended_node_span_start = node_span_start.copy() if isinstance(node_span_start, dict) else node_span_start
            extended_node_span_end = fork_node_info
        
        # Create extended span
        extended_span = node_span_row.copy()
        extended_span["geometry"] = LineString(node_span_coords)
        extended_span["start"] = json.dumps(convert_to_serializable(extended_node_span_start))
        extended_span["end"] = json.dumps(convert_to_serializable(extended_node_span_end))
        new_spans.append(extended_span)
        spans_to_remove.append(node_span_idx)
        
        # 2. Split the target span at the fork point
        target_coords = list(target_span_line.coords)
        split_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
        
        # Find where to insert the split point by finding the closest segment
        insert_index = None
        min_dist_to_segment = float("inf")
        
        for i in range(len(target_coords) - 1):
            seg_line = LineString([target_coords[i], target_coords[i + 1]])
            dist_to_seg = nearest_point_on_span.distance(seg_line)
            if dist_to_seg < min_dist_to_segment:
                min_dist_to_segment = dist_to_seg
                # Determine insertion point based on which end of segment is closer
                seg_start_pt = Point(target_coords[i])
                seg_end_pt = Point(target_coords[i + 1])
                dist_to_start = nearest_point_on_span.distance(seg_start_pt)
                dist_to_end = nearest_point_on_span.distance(seg_end_pt)
                
                if dist_to_start < 1e-6:
                    # Point is at start of segment
                    insert_index = i
                elif dist_to_end < 1e-6:
                    # Point is at end of segment
                    insert_index = i + 1
                else:
                    # Point is somewhere in the middle of the segment
                    insert_index = i + 1
        
        if insert_index is None:
            node_processing_status[node_id] = "SKIPPED: Could not determine split location"
            continue
        
        # Check if point already exists at this index (within tolerance)
        point_exists = False
        if insert_index < len(target_coords):
            existing_pt = Point(target_coords[insert_index])
            if existing_pt.distance(nearest_point_on_span) < 1e-6:
                point_exists = True
        
        if not point_exists:
            target_coords.insert(insert_index, split_coord)
        
        # Create two segments
        segment1_coords = target_coords[:insert_index + 1]
        segment2_coords = target_coords[insert_index:]
        
        if len(segment1_coords) < 2 or len(segment2_coords) < 2:
            node_processing_status[node_id] = "SKIPPED: Insufficient points for segments"
            continue
        
        segment1 = LineString(segment1_coords)
        segment2 = LineString(segment2_coords)
        
        # Get target span endpoints
        target_start = (
            json.loads(target_span_row["start"])
            if isinstance(target_span_row["start"], str)
            else target_span_row["start"]
        )
        target_end = (
            json.loads(target_span_row["end"])
            if isinstance(target_span_row["end"], str)
            else target_span_row["end"]
        )
        
        # Determine which segment connects to which endpoint
        seg1_start_pt = Point(segment1.coords[0])
        seg1_end_pt = Point(segment1.coords[-1])
        target_start_pt = Point(target_start["location"]["coordinates"]) if target_start else None
        
        if target_start_pt and seg1_start_pt.distance(target_start_pt) < 1e-3:
            # Segment1 connects to start
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment1
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment2
        elif target_start_pt and seg1_end_pt.distance(target_start_pt) < 1e-3:
            # Segment1 reversed connects to start
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment2
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment1
        else:
            # Segment1 connects to end
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment2
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment1
        
        # Create two new spans from the split
        split_span1 = target_span_row.copy()
        split_span1["id"] = str(uuid.uuid4())
        split_span1["geometry"] = new_span1_geom
        split_span1["start"] = json.dumps(convert_to_serializable(new_span1_start))
        split_span1["end"] = json.dumps(convert_to_serializable(new_span1_end))
        
        split_span2 = target_span_row.copy()
        split_span2["id"] = str(uuid.uuid4())
        split_span2["geometry"] = new_span2_geom
        split_span2["start"] = json.dumps(convert_to_serializable(new_span2_start))
        split_span2["end"] = json.dumps(convert_to_serializable(new_span2_end))
        
        new_spans.append(split_span1)
        new_spans.append(split_span2)
        spans_to_remove.append(nearest_span_idx)
        
        # Mark node for renaming and moving
        nodes_to_rename[node_id] = nearest_point_on_span
        dist_str = f"{(min_span_distance / METERS_TO_DEGREES):.2f}"
        node_processing_status[node_id] = f"PROCESSED: Created fork ({dist_str}m from span {nearest_span_idx})"

    # Remove old spans and add new ones
    if spans_to_remove:
        gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove)
        if new_spans:
            new_spans_gdf = gpd.GeoDataFrame(new_spans, crs=gdf_ofds_spans.crs)
            gdf_ofds_spans = pd.concat(
                [gdf_ofds_spans, new_spans_gdf], ignore_index=True
            )

    # Update node geometries and names
    for node_id, new_geometry in nodes_to_rename.items():
        node_matches = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id]
        if len(node_matches) == 0:
            print(f"  [WARNING] Phase 6: Node {node_id[:8]}... not found for renaming")
            continue
        node_idx = node_matches.index[0]
        old_name = gdf_ofds_nodes.at[node_idx, "name"]
        gdf_ofds_nodes.at[node_idx, "geometry"] = new_geometry
        gdf_ofds_nodes.at[node_idx, "name"] = "network fork"
        print(f"  [DEBUG] Phase 6: Renamed node {node_id[:8]}... from '{old_name}' to 'network fork'")

    # Add processing status field to nodes for debugging
    if "phase6_status" not in gdf_ofds_nodes.columns:
        gdf_ofds_nodes["phase6_status"] = "Not processed"
    
    for node_id, status in node_processing_status.items():
        node_idx = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].index
        if len(node_idx) > 0:
            gdf_ofds_nodes.at[node_idx[0], "phase6_status"] = status
    
    # Print summary of processing status
    processed_count = sum(1 for s in node_processing_status.values() if s.startswith("PROCESSED"))
    skipped_count = len(node_processing_status) - processed_count
    total_nodes = len(auto_gen_endpoint_nodes)
    unprocessed_count = total_nodes - len(node_processing_status)
    
    print(f"Phase 6: Processed {processed_count} nodes, skipped {skipped_count} nodes, {unprocessed_count} nodes not evaluated")
    
    if nodes_to_rename:
        print(
            f"Phase 6: Moved and renamed {len(nodes_to_rename)} nodes to fork points, "
            f"split {len(spans_to_remove)} spans"
        )
    
    # Print details for skipped nodes (for debugging)
    if skipped_count > 0:
        print("  Phase 6 skipped nodes:")
        for node_id, status in node_processing_status.items():
            if status.startswith("SKIPPED"):
                node_idx = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].index
                if len(node_idx) > 0:
                    node_name = gdf_ofds_nodes.at[node_idx[0], "name"]
                    print(f"    Node {node_id[:8]}... ({node_name}): {status}")
    
    return gdf_ofds_nodes, gdf_ofds_spans


def _split_spans_at_proper_nodes(gdf_ofds_nodes, gdf_ofds_spans, threshold):
    """
    Phase 7: Split spans at proper nodes that are near spans but not endpoints.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes (modified in place)
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        threshold: Distance threshold in degrees
        
    Returns:
        tuple: (gdf_ofds_nodes, gdf_ofds_spans) - Updated GeoDataFrames
    """
    # Process proper nodes (not auto-generated) that are not already span endpoints
    start_ids = gdf_ofds_spans["start"].apply(_extract_id)
    end_ids = gdf_ofds_spans["end"].apply(_extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())

    proper_nodes = gdf_ofds_nodes[
        (gdf_ofds_nodes["name"] != "Auto generated missing node") &
        (gdf_ofds_nodes["name"] != "network fork") &
        (~gdf_ofds_nodes["id"].isin(span_endpoint_ids))
    ]

    spans_to_remove_proper = []
    new_spans_proper = []

    for node_idx, node_row in proper_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        node_name = node_row["name"]

        # Find spans where this node is currently an endpoint
        current_span_ids = []
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove_proper:
                continue
            span_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            span_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )
            if (isinstance(span_start, dict) and span_start.get("id") == node_id) or (
                isinstance(span_end, dict) and span_end.get("id") == node_id
            ):
                current_span_ids.append(span_idx)

        # Find nearest span (excluding spans where this node is already an endpoint)
        min_span_distance = float("inf")
        nearest_span_idx = None
        nearest_point_on_span = None

        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove_proper or span_idx in current_span_ids:
                continue
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_idx = span_idx
                nearest_point_on_span = nearest_point

        # If within threshold, split span and connect to proper node
        if (
            min_span_distance <= threshold
            and nearest_span_idx is not None
            and nearest_point_on_span is not None
        ):
            span_row = gdf_ofds_spans.loc[nearest_span_idx]
            span_line = span_row.geometry

            # Check that nearest point is not at span endpoints
            start_point = Point(span_line.coords[0])
            end_point = Point(span_line.coords[-1])
            endpoint_tolerance = 1e-6
            if (
                nearest_point_on_span.distance(start_point) < endpoint_tolerance
                or nearest_point_on_span.distance(end_point) < endpoint_tolerance
            ):
                continue

            # Insert the split point into the LineString coordinates
            coords = list(span_line.coords)
            split_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
            
            # Find the index where to insert the split point
            insert_index = None
            min_dist_to_segment = float("inf")
            
            for i in range(len(coords) - 1):
                seg_start = Point(coords[i])
                seg_end = Point(coords[i + 1])
                seg_line = LineString([coords[i], coords[i + 1]])
                
                dist_to_seg = nearest_point_on_span.distance(seg_line)
                if dist_to_seg < min_dist_to_segment:
                    min_dist_to_segment = dist_to_seg
                    dist_to_start = nearest_point_on_span.distance(seg_start)
                    dist_to_end = nearest_point_on_span.distance(seg_end)
                    
                    if dist_to_start < 1e-9:
                        insert_index = i + 1
                        break
                    elif dist_to_end < 1e-9:
                        insert_index = i + 1
                        break
                    else:
                        insert_index = i + 1
            
            # Insert the split point into coordinates
            if insert_index is not None:
                point_already_exists = False
                for coord in coords:
                    if Point(coord).distance(nearest_point_on_span) < 1e-9:
                        point_already_exists = True
                        insert_index = coords.index(coord)
                        break
                
                if not point_already_exists:
                    coords.insert(insert_index, split_coord)
            
            # Create two segments from the coordinates
            if insert_index is None or len(coords) < 3:
                continue
            
            # Split at the inserted point
            segment1_coords = coords[:insert_index + 1]
            segment2_coords = coords[insert_index:]
            
            # Ensure segments have at least 2 points
            if len(segment1_coords) < 2 or len(segment2_coords) < 2:
                continue
            
            segment1 = LineString(segment1_coords)
            segment2 = LineString(segment2_coords)

            # Get original span's start and end node info
            original_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            original_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )

            # Extend segments to the proper node's actual location
            seg1_end_point = Point(segment1.coords[-1])
            if seg1_end_point.distance(node_point) > 1e-9:
                segment1_coords_extended = list(segment1.coords) + [(node_point.x, node_point.y)]
                segment1 = LineString(segment1_coords_extended)
            
            seg2_start_point = Point(segment2.coords[0])
            if seg2_start_point.distance(node_point) > 1e-9:
                segment2_coords_extended = [(node_point.x, node_point.y)] + list(segment2.coords)
                segment2 = LineString(segment2_coords_extended)

            # Determine which segment connects to which endpoint
            seg1_start = Point(segment1.coords[0])
            seg1_end = Point(segment1.coords[-1])
            seg2_start = Point(segment2.coords[0])

            original_start_point = (
                Point(original_start["location"]["coordinates"])
                if original_start
                else None
            )
            original_end_point = (
                Point(original_end["location"]["coordinates"])
                if original_end
                else None
            )

            # Verify the node still exists in gdf_ofds_nodes before creating reference
            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].empty
            if not node_exists:
                print(
                    f"WARNING: Node ID '{node_id}' (name: '{node_name}') "
                    f"not found in nodes GeoDataFrame. Skipping span split."
                )
                continue
            
            # Create node info for the proper node
            proper_node_info = {
                "id": node_id,
                "name": node_name,
                "location": {
                    "type": "Point",
                    "coordinates": [
                        node_point.x,
                        node_point.y,
                    ],
                },
            }

            # Determine segment assignments
            if (
                original_start_point
                and seg1_start.distance(original_start_point) < 1e-3
            ):
                new_span1_start = original_start
                new_span1_end = proper_node_info
                new_span1_geom = segment1
                new_span2_start = proper_node_info
                new_span2_end = original_end
                new_span2_geom = segment2
            elif (
                original_start_point
                and seg2_start.distance(original_start_point) < 1e-3
            ):
                new_span1_start = original_start
                new_span1_end = proper_node_info
                new_span1_geom = segment2
                new_span2_start = proper_node_info
                new_span2_end = original_end
                new_span2_geom = segment1
            else:
                seg1_matches_end = (
                    original_end_point
                    and seg1_end.distance(original_end_point) < 1e-3
                )
                if seg1_matches_end:
                    new_span1_start = original_start
                    new_span1_end = proper_node_info
                    new_span1_geom = segment2
                    new_span2_start = proper_node_info
                    new_span2_end = original_end
                    new_span2_geom = segment1
                else:
                    new_span1_start = original_start
                    new_span1_end = proper_node_info
                    new_span1_geom = segment1
                    new_span2_start = proper_node_info
                    new_span2_end = original_end
                    new_span2_geom = segment2

            # Create new span records
            new_span1 = span_row.copy()
            new_span1["id"] = str(uuid.uuid4())
            new_span1["geometry"] = new_span1_geom
            new_span1["start"] = json.dumps(
                convert_to_serializable(new_span1_start)
            )
            new_span1["end"] = json.dumps(
                convert_to_serializable(new_span1_end)
            )

            new_span2 = span_row.copy()
            new_span2["id"] = str(uuid.uuid4())
            new_span2["geometry"] = new_span2_geom
            new_span2["start"] = json.dumps(
                convert_to_serializable(new_span2_start)
            )
            new_span2["end"] = json.dumps(
                convert_to_serializable(new_span2_end)
            )

            new_spans_proper.append(new_span1)
            new_spans_proper.append(new_span2)
            spans_to_remove_proper.append(nearest_span_idx)

    # Remove old spans and add new ones
    if spans_to_remove_proper:
        gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove_proper)
        if new_spans_proper:
            new_spans_gdf = gpd.GeoDataFrame(new_spans_proper, crs=gdf_ofds_spans.crs)
            gdf_ofds_spans = pd.concat(
                [gdf_ofds_spans, new_spans_gdf], ignore_index=True
            )

    if spans_to_remove_proper:
        print(
            f"Phase 7: Processed {len(proper_nodes)} proper nodes near spans, "
            f"split {len(spans_to_remove_proper)} spans"
        )
    else:
        print(f"Phase 7: Processed {len(proper_nodes)} proper nodes near spans, no spans split")
    
    return gdf_ofds_nodes, gdf_ofds_spans


def _remove_duplicate_spans(gdf_ofds_spans):
    """
    Phase 8: Remove bidirectional duplicate spans.
    
    Args:
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        
    Returns:
        GeoDataFrame: Updated spans GeoDataFrame
    """
    print("\nPhase 8: Removing bidirectional duplicate spans...")
    spans_before_dedup = len(gdf_ofds_spans)
    
    # Create a normalized representation: (min(start_id, end_id), max(start_id, end_id))
    # This treats A->B and B->A as the same connection
    normalized_connections = {}
    spans_to_keep = []
    
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_id = None
        end_id = None
        
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
        
        # Skip spans without both start and end nodes
        if start_id is None or end_id is None:
            spans_to_keep.append(index)
            continue
        
        # Create normalized connection key (sorted tuple to treat A->B and B->A as same)
        connection_key = tuple(sorted([start_id, end_id]))
        
        # Check if we've seen this connection before
        if connection_key not in normalized_connections:
            # First time seeing this connection - keep it
            normalized_connections[connection_key] = index
            spans_to_keep.append(index)
        else:
            # Duplicate connection (A->B vs B->A) - check if geometries are the same or reversed
            existing_index = normalized_connections[connection_key]
            existing_span = gdf_ofds_spans.loc[existing_index]
            existing_geom = existing_span["geometry"]
            current_geom = span["geometry"]
            
            # Check if geometries are the same or reversed
            coords_match = (
                list(existing_geom.coords) == list(current_geom.coords)
                or list(existing_geom.coords) == list(current_geom.coords)[::-1]
            )
            
            if coords_match:
                # Duplicate span (same connection, same geometry) - skip it
                continue
            else:
                # Same nodes but different geometry - this might be intentional (different paths)
                # For now, keep the first one and skip duplicates to avoid bidirectional duplicates
                # If you need to keep multiple paths between the same nodes, this can be adjusted
                continue
    
    # Filter to keep only non-duplicate spans
    gdf_ofds_spans = gdf_ofds_spans.loc[spans_to_keep].copy()
    spans_removed = spans_before_dedup - len(gdf_ofds_spans)
    
    if spans_removed > 0:
        print(f"Phase 8: Removed {spans_removed} bidirectional duplicate spans")
    else:
        print("Phase 8: No bidirectional duplicates found")
    
    return gdf_ofds_spans


def _rename_spans_from_nodes(gdf_ofds_spans):
    """
    Phase 9: Rename spans based on start and end node names.
    
    Args:
        gdf_ofds_spans: GeoDataFrame containing spans (modified in place)
        
    Returns:
        GeoDataFrame: Updated spans GeoDataFrame
    """
    print("\nPhase 9: Renaming spans from node names...")
    spans_renamed = 0
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_name = None
        end_name = None
        
        if start_dict is not None and isinstance(start_dict, dict):
            start_name = start_dict.get("name")
        
        if end_dict is not None and isinstance(end_dict, dict):
            end_name = end_dict.get("name")
        
        # Create new name: "start name - end name"
        if start_name and end_name:
            new_name = f"{start_name} - {end_name}"
            gdf_ofds_spans.at[index, "name"] = new_name
            spans_renamed += 1
        elif start_name:
            # Only start node has a name
            gdf_ofds_spans.at[index, "name"] = start_name
            spans_renamed += 1
        elif end_name:
            # Only end node has a name
            gdf_ofds_spans.at[index, "name"] = end_name
            spans_renamed += 1
        # If neither has a name, leave the span name unchanged
    
    print(f"Phase 9: Renamed {spans_renamed} spans from node names")
    
    return gdf_ofds_spans


def _validate_node_references(gdf_ofds_nodes, gdf_ofds_spans):
    """
    Phase 10: Validate that all span node references exist in the nodes GeoDataFrame.
    
    Args:
        gdf_ofds_nodes: GeoDataFrame containing nodes
        gdf_ofds_spans: GeoDataFrame containing spans
        
    Returns:
        None (prints warnings if issues found)
    """
    print("\nPhase 10: Validating node references in spans...")
    valid_node_ids = set(gdf_ofds_nodes["id"].unique())
    invalid_spans = []
    
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_id = None
        end_id = None
        
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
        
        issues = []
        if start_id is not None and start_id not in valid_node_ids:
            issues.append(f"start node ID '{start_id}' not found")
        if end_id is not None and end_id not in valid_node_ids:
            issues.append(f"end node ID '{end_id}' not found")
        
        if issues:
            invalid_spans.append({
                "span_id": span.get("id", f"span_{index}"),
                "span_name": span.get("name", "unknown"),
                "issues": issues,
                "start_id": start_id,
                "end_id": end_id
            })
    
    if invalid_spans:
        print(f"WARNING: Found {len(invalid_spans)} spans with invalid node references:")
        for invalid in invalid_spans[:10]:  # Show first 10
            print(f"  - Span '{invalid['span_name']}' (ID: {invalid['span_id']}): {', '.join(invalid['issues'])}")
        if len(invalid_spans) > 10:
            print(f"  ... and {len(invalid_spans) - 10} more")
        print("\nThese spans reference nodes that don't exist in the nodes array.")
        print("This can happen when:")
        print("  1. Nodes are removed during consolidation but spans aren't updated")
        print("  2. Spans are created with node references before nodes are finalized")
        print("  3. Node ID lookups fail silently during merge operations")
    else:
        print("Phase 10: All spans have valid node references")


def consolidate_auto_generated_nodes(
    gdf_ofds_nodes,
    gdf_ofds_spans,
    threshold_meters,
    debug_enabled=False,
    debug_output_dir=None,
    debug_output_prefix="",
    rename_spans_from_nodes=False,
):
    """
    Consolidated function that analyzes, merges, and splits spans at auto-generated nodes.

    This function performs the following operations:
    1. Analyzes auto-generated nodes and prints distances to nearest nodes and spans
    2. Merges auto-generated nodes that are close to each other
    3. Merges auto-generated nodes that are close to proper nodes
    4. Moves and splits spans at auto-generated endpoint nodes to create fork points
    5. Optionally renames spans based on start and end node names

    Args:
        gdf_ofds_nodes (GeoDataFrame): GeoDataFrame containing the node points.
        gdf_ofds_spans (GeoDataFrame): GeoDataFrame containing the spans.
        threshold_meters (float): Distance threshold in meters for merging and splitting operations.
        debug_enabled (bool): If True, write debug GeoJSON files after each phase.
        debug_output_dir (str): Output directory for debug files.
        debug_output_prefix (str): Prefix for debug filenames.
        rename_spans_from_nodes (bool): If True, rename spans to "start node name - end node name".

    Returns:
        tuple: (gdf_ofds_spans, gdf_ofds_nodes) - Updated spans and nodes GeoDataFrames.
    """
    # Validate debug configuration
    if debug_enabled and debug_output_dir is None:
        print("  [DEBUG] Warning: debug_enabled is True but debug_output_dir is None. Debug files will not be written.")
        debug_enabled = False
    
    # Phase 1: Setup
    # Convert threshold from meters to degrees
    # 1 degree ≈ 111 km = 111,000 meters
    METERS_TO_DEGREES = 1.0 / 111000.0
    threshold = threshold_meters * METERS_TO_DEGREES

    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]

    if len(auto_gen_nodes) == 0:
        print("No auto-generated nodes to process.")
        return gdf_ofds_spans, gdf_ofds_nodes

    other_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] != "Auto generated missing node"
    ]

    DEGREES_TO_KM = 111.0

    def extract_id(x):
        if isinstance(x, dict):
            return x.get("id")
        elif isinstance(x, str):
            try:
                return json.loads(x).get("id")
            except json.JSONDecodeError:
                return x
        return x

    start_ids = gdf_ofds_spans["start"].apply(extract_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())

    # Phase 2: Analysis
    print(f"\nAnalyzing {len(auto_gen_nodes)} auto-generated nodes:")
    print("-" * 80)

    for node_idx, node_row in auto_gen_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        is_endpoint = node_id in span_endpoint_ids

        min_node_distance = float("inf")
        nearest_node_id = None

        if len(other_nodes) > 0:
            for other_idx, other_row in other_nodes.iterrows():
                other_point = other_row.geometry
                distance = node_point.distance(other_point)
                if distance < min_node_distance:
                    min_node_distance = distance
                    nearest_node_id = other_row["id"]

        min_span_distance = float("inf")
        nearest_span_id = None
        nearest_point_on_span = None

        for span_idx, span_row in gdf_ofds_spans.iterrows():
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_id = span_row.get("id", f"span_{span_idx}")
                nearest_point_on_span = nearest_point

        status = "endpoint" if is_endpoint else "isolated"
 
    # Write debug files before Phase 3 (initial state)
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase2", debug_output_prefix)

    # Phase 3: Merge Auto-Generated with Proper Nodes
    # Find all clusters where auto-generated nodes are within threshold of proper nodes
    coordinates = np.array([(point.x, point.y) for point in gdf_ofds_nodes.geometry])
    tree = KDTree(coordinates)
    clusters = [
        indices
        for indices in tree.query_radius(coordinates, r=threshold)
        if len(indices) > 1
    ]

    # Build a mapping: auto_generated_node_id -> proper_node_id
    # This ensures ALL auto-generated nodes within threshold get merged
    auto_to_proper_mapping = {}
    
    for cluster in clusters:
        # Separate auto-generated nodes from proper nodes in this cluster
        auto_gen_indices = []
        proper_indices = []
        
        for idx in cluster:
            node_name = gdf_ofds_nodes.iloc[idx]["name"]
            if node_name == "Auto generated missing node":
                auto_gen_indices.append(idx)
            else:
                proper_indices.append(idx)
        
        # If we have auto-generated nodes and at least one proper node, merge them
        if auto_gen_indices and proper_indices:
            # Use the first proper node as the target (all auto-generated nodes merge to it)
            proper_node_idx = proper_indices[0]
            proper_node_id = gdf_ofds_nodes.iloc[proper_node_idx]["id"]
            
            # Map ALL auto-generated nodes in this cluster to the proper node
            for auto_idx in auto_gen_indices:
                auto_node_id = gdf_ofds_nodes.iloc[auto_idx]["id"]
                auto_to_proper_mapping[auto_node_id] = proper_node_id

    # Update all spans that reference auto-generated nodes to reference proper nodes instead
    merged_node_ids = []
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_updated = False
        end_updated = False
        
        # Check and update start node
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
            if start_id in auto_to_proper_mapping:
                proper_node_id = auto_to_proper_mapping[start_id]
                # Find the proper node
                proper_node_row = gdf_ofds_nodes[gdf_ofds_nodes["id"] == proper_node_id]
                if not proper_node_row.empty:
                    proper_node = proper_node_row.iloc[0]
                    proper_node_geometry = proper_node["geometry"]
                    
                    # Update the node ID, name, and location
                    start_dict["id"] = proper_node["id"]
                    start_dict["name"] = proper_node["name"]
                    if "location" in start_dict:
                        start_dict["location"]["coordinates"] = [
                            proper_node_geometry.x,
                            proper_node_geometry.y,
                        ]
                    
                    merged_node_ids.append(start_id)
                    start_updated = True
                    
                    # Update the span geometry endpoint to match the proper node
                    span_geometry = span["geometry"]
                    updated_coords = list(span_geometry.coords)
                    updated_coords[0] = (proper_node_geometry.x, proper_node_geometry.y)
                    span_geometry = LineString(updated_coords)
                    gdf_ofds_spans.at[index, "geometry"] = span_geometry
                else:
                    # Proper node not found - this shouldn't happen but log it
                    span_name = span.get('name', 'unknown')
                    print(
                        f"WARNING: Proper node ID '{proper_node_id}' not found for "
                        f"auto-generated node '{start_id}' in span '{span_name}'. "
                        f"Span will retain reference to auto-generated node."
                    )
        
        # Check and update end node independently
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
            if end_id in auto_to_proper_mapping:
                proper_node_id = auto_to_proper_mapping[end_id]
                # Find the proper node
                proper_node_row = gdf_ofds_nodes[gdf_ofds_nodes["id"] == proper_node_id]
                if not proper_node_row.empty:
                    proper_node = proper_node_row.iloc[0]
                    proper_node_geometry = proper_node["geometry"]
                    
                    # Update the node ID, name, and location
                    end_dict["id"] = proper_node["id"]
                    end_dict["name"] = proper_node["name"]
                    if "location" in end_dict:
                        end_dict["location"]["coordinates"] = [
                            proper_node_geometry.x,
                            proper_node_geometry.y,
                        ]
                    
                    merged_node_ids.append(end_id)
                    end_updated = True
                    
                    # Update the span geometry endpoint to match the proper node
                    span_geometry = span["geometry"]
                    updated_coords = list(span_geometry.coords)
                    updated_coords[-1] = (proper_node_geometry.x, proper_node_geometry.y)
                    span_geometry = LineString(updated_coords)
                    gdf_ofds_spans.at[index, "geometry"] = span_geometry
                else:
                    # Proper node not found - this shouldn't happen but log it
                    span_name = span.get('name', 'unknown')
                    print(
                        f"WARNING: Proper node ID '{proper_node_id}' not found for "
                        f"auto-generated node '{end_id}' in span '{span_name}'. "
                        f"Span will retain reference to auto-generated node."
                    )
        
        # Update span endpoints if they were modified
        if start_updated or end_updated:
            start_json = json.dumps(convert_to_serializable(start_dict))
            end_json = json.dumps(convert_to_serializable(end_dict))
            gdf_ofds_spans.at[index, "start"] = start_json
            gdf_ofds_spans.at[index, "end"] = end_json

    # Remove all merged auto-generated nodes
    gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(merged_node_ids)]
    print(
        f"Phase 3: Merged {len(set(merged_node_ids))} auto-generated nodes "
        f"with proper nodes. Remaining nodes: {len(gdf_ofds_nodes)}"
    )
    
    # Write debug files after Phase 3
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase3", debug_output_prefix)

    # Phase 4: Merge 3+ Auto-Generated Node Clusters
    # Re-filter auto-generated nodes after Phase 3 merges
    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]
    filtered_nodes = auto_gen_nodes.copy()
    coordinates = np.array([(point.x, point.y) for point in filtered_nodes.geometry])
    
    nodes_to_remove_phase4 = set()
    new_fork_nodes = []
    
    if len(coordinates) > 0:
        tree = KDTree(coordinates)
        # Find all clusters of nodes within threshold
        all_clusters = [
            indices
            for indices in tree.query_radius(coordinates, r=threshold)
            if len(indices) >= 2  # Process clusters with 2+ nodes
        ]
        
        # Separate clusters into pairs (2 nodes) and larger clusters (3+ nodes)
        pair_clusters = [c for c in all_clusters if len(c) == 2]
        larger_clusters = [c for c in all_clusters if len(c) >= 3]
        
        # Remove duplicate clusters (same nodes, different order)
        def normalize_cluster(cluster):
            return tuple(sorted(cluster))
        
        unique_larger_clusters = list(set([normalize_cluster(c) for c in larger_clusters]))

        # Process larger clusters (3+ nodes):
        # Merge them into a single "network fork" node
        # All spans connected to those nodes terminate at the "network fork" node
        for cluster in unique_larger_clusters:
            # Get all node IDs in this cluster
            cluster_node_ids = [filtered_nodes.iloc[idx]["id"] for idx in cluster]
            cluster_node_indices = list(cluster)
            # Get all node IDs in this cluster
            cluster_node_ids = [filtered_nodes.iloc[idx]["id"] for idx in cluster]
            cluster_node_indices = list(cluster)
            
            # Calculate centroid of all nodes in cluster
            cluster_points = [filtered_nodes.iloc[idx].geometry for idx in cluster]
            centroid_x = sum(p.x for p in cluster_points) / len(cluster_points)
            centroid_y = sum(p.y for p in cluster_points) / len(cluster_points)
            fork_location = Point(centroid_x, centroid_y)
            
            # Get network info from first node in cluster
            first_node_row = filtered_nodes.iloc[cluster_node_indices[0]]
            network_info = first_node_row.get("network", {})
            physical_infrastructure_provider = first_node_row.get("physicalInfrastructureProvider", {})
            network_providers = first_node_row.get("networkProviders", [])
            
            # Create new "network fork" node
            fork_node_id = str(uuid.uuid4())
            fork_node = {
                "id": fork_node_id,
                "name": "network fork",
                "geometry": fork_location,
                "network": network_info,
                "physicalInfrastructureProvider": physical_infrastructure_provider,
                "networkProviders": network_providers,
                "featureType": "node",
            }
            new_fork_nodes.append(fork_node)
            
            # Find all spans connected to any node in the cluster
            connected_spans = []
            for span_idx, span_row in gdf_ofds_spans.iterrows():
                start_dict = (
                    json.loads(span_row["start"])
                    if isinstance(span_row["start"], str) and span_row["start"] is not None
                    else span_row["start"]
                )
                end_dict = (
                    json.loads(span_row["end"])
                    if isinstance(span_row["end"], str) and span_row["end"] is not None
                    else span_row["end"]
                )
                
                start_id = start_dict.get("id") if isinstance(start_dict, dict) else None
                end_id = end_dict.get("id") if isinstance(end_dict, dict) else None
                
                # Get node names to check if endpoints are proper nodes
                start_name = start_dict.get("name") if isinstance(start_dict, dict) else None
                end_name = end_dict.get("name") if isinstance(end_dict, dict) else None
                
                # Skip spans that have proper nodes as endpoints (already merged in Phase 3)
                # Phase 4 should only process spans between auto-generated nodes
                if (start_name and start_name != "Auto generated missing node" and start_name != "network fork") or \
                   (end_name and end_name != "Auto generated missing node" and end_name != "network fork"):
                    # This span has a proper node endpoint - skip it
                    continue
                
                # Check if span is connected to any node in cluster
                if start_id in cluster_node_ids or end_id in cluster_node_ids:
                    connected_spans.append((
                        span_idx, span_row,
                        start_id in cluster_node_ids,
                        end_id in cluster_node_ids
                    ))
            
            # Update all connected spans to point to fork node
            fork_node_info = {
                "id": fork_node_id,
                "name": "network fork",
                "location": {
                    "type": "Point",
                    "coordinates": [centroid_x, centroid_y],
                },
            }
            
            for span_idx, span_row, start_in_cluster, end_in_cluster in connected_spans:
                start_dict = (
                    json.loads(span_row["start"])
                    if isinstance(span_row["start"], str) and span_row["start"] is not None
                    else span_row["start"]
                )
                end_dict = (
                    json.loads(span_row["end"])
                    if isinstance(span_row["end"], str) and span_row["end"] is not None
                    else span_row["end"]
                )
                
                span_geometry = span_row["geometry"]
                updated_coords = list(span_geometry.coords)
                updated = False
                
                # Update start endpoint if it's in cluster
                if start_in_cluster and isinstance(start_dict, dict):
                    start_dict["id"] = fork_node_id
                    start_dict["name"] = "network fork"
                    if "location" in start_dict:
                        start_dict["location"]["coordinates"] = [centroid_x, centroid_y]
                    # Extend geometry to fork location
                    updated_coords[0] = (centroid_x, centroid_y)
                    updated = True
                
                # Update end endpoint if it's in cluster
                if end_in_cluster and isinstance(end_dict, dict):
                    end_dict["id"] = fork_node_id
                    end_dict["name"] = "network fork"
                    if "location" in end_dict:
                        end_dict["location"]["coordinates"] = [centroid_x, centroid_y]
                    # Extend geometry to fork location
                    updated_coords[-1] = (centroid_x, centroid_y)
                    updated = True
                
                if updated:
                    # Remove duplicate consecutive coordinates
                    cleaned_coords = [updated_coords[0]]
                    for coord in updated_coords[1:]:
                        if coord != cleaned_coords[-1]:
                            cleaned_coords.append(coord)
                    
                    # Ensure we have at least 2 coordinates for a valid LineString
                    if len(cleaned_coords) >= 2:
                        span_geometry = LineString(cleaned_coords)
                        gdf_ofds_spans.at[span_idx, "geometry"] = span_geometry
                        gdf_ofds_spans.at[span_idx, "start"] = json.dumps(convert_to_serializable(start_dict))
                        gdf_ofds_spans.at[span_idx, "end"] = json.dumps(convert_to_serializable(end_dict))
                    else:
                        # Skip spans that would have invalid geometry (only 1 point)
                        print(f"Warning: Skipping span {span_idx} - insufficient coordinates after cleaning ({len(cleaned_coords)} point(s))")
            
            # Mark all nodes in cluster for removal
            for node_id in cluster_node_ids:
                nodes_to_remove_phase4.add(node_id)
        
        # Add new fork nodes to the nodes GeoDataFrame
        if new_fork_nodes:
            fork_nodes_gdf = gpd.GeoDataFrame(new_fork_nodes, crs=gdf_ofds_nodes.crs)
            gdf_ofds_nodes = pd.concat([gdf_ofds_nodes, fork_nodes_gdf], ignore_index=True)

        # Remove the nodes processed in Phase 4
        if nodes_to_remove_phase4:
            gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(list(nodes_to_remove_phase4))]
            clusters_processed = len(unique_larger_clusters)
            fork_nodes_created = len(new_fork_nodes)
            print(
                f"Phase 4: Processed {clusters_processed} clusters (3+ nodes). "
                f"Removed {len(nodes_to_remove_phase4)} auto-generated nodes, "
                f"created {fork_nodes_created} network fork nodes. "
                f"Remaining nodes: {len(gdf_ofds_nodes)}"
            )
    
    # Write debug files after Phase 4
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase4", debug_output_prefix)

    # Phase 5: Merge 2 Auto-Generated Node Pairs
    # Re-filter auto-generated nodes after Phase 4 merges
    auto_gen_nodes = gdf_ofds_nodes[
        gdf_ofds_nodes["name"] == "Auto generated missing node"
    ]
    filtered_nodes = auto_gen_nodes.copy()
    coordinates = np.array([(point.x, point.y) for point in filtered_nodes.geometry])
    
    nodes_to_remove_phase45 = set()
    spans_to_remove = []
    new_spans = []
    
    if len(coordinates) > 0:
        tree = KDTree(coordinates)
        # Find all clusters of nodes within threshold
        all_clusters = [
            indices
            for indices in tree.query_radius(coordinates, r=threshold)
            if len(indices) >= 2  # Process clusters with 2+ nodes
        ]
        
        # Get only pairs (2 nodes)
        pair_clusters = [c for c in all_clusters if len(c) == 2]
        
        # Remove duplicate clusters (same nodes, different order)
        def normalize_cluster(cluster):
            return tuple(sorted(cluster))
        
        unique_pair_clusters = list(set([normalize_cluster(c) for c in pair_clusters]))

        # Convert pair clusters to pairs (each cluster of 2 nodes becomes one pair)
        unique_pairs = [
            (min(cluster[0], cluster[1]), max(cluster[0], cluster[1]))
            for cluster in unique_pair_clusters
        ]
        # Remove duplicate pairs
        unique_pairs = list(set(unique_pairs))

        for pair in unique_pairs:
            node_a_idx = pair[0]
            node_b_idx = pair[1]
            node_a_id = filtered_nodes.iloc[node_a_idx]["id"]
            node_b_id = filtered_nodes.iloc[node_b_idx]["id"]

            # Find all spans connected to Node A
            spans_connected_to_a = []
            spans_connected_to_b = []

            for span_idx, span_row in gdf_ofds_spans.iterrows():
                try:
                    start_dict = (
                        json.loads(span_row["start"])
                        if isinstance(span_row["start"], str) and span_row["start"] is not None
                        else span_row["start"]
                    )
                    end_dict = (
                        json.loads(span_row["end"])
                        if isinstance(span_row["end"], str) and span_row["end"] is not None
                        else span_row["end"]
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"WARNING: Failed to parse span {span_idx} start/end JSON: {e}. Skipping.")
                    continue
                
                start_id = start_dict.get("id") if isinstance(start_dict, dict) else None
                end_id = end_dict.get("id") if isinstance(end_dict, dict) else None
                
                # Check if span is connected to Node A or Node B
                connected_to_a = (start_id == node_a_id or end_id == node_a_id)
                connected_to_b = (start_id == node_b_id or end_id == node_b_id)
                
                # Only process spans that are connected to at least one of our target nodes
                if not (connected_to_a or connected_to_b):
                    continue

                # Check if span is connected to Node A
                if connected_to_a:
                    spans_connected_to_a.append((span_idx, span_row, start_id == node_a_id))

                # Check if span is connected to Node B
                if connected_to_b:
                    spans_connected_to_b.append((span_idx, span_row, start_id == node_b_id))

            # Join spans: combine all spans connected to Node A and Node B
            # Deduplicate spans (a span connecting A to B will appear in both lists)
            unique_connected_spans = {}
            for span_idx, span_row, is_start_a in spans_connected_to_a:
                if span_idx not in unique_connected_spans:
                    unique_connected_spans[span_idx] = (span_row, is_start_a, False)
            for span_idx, span_row, is_start_b in spans_connected_to_b:
                if span_idx not in unique_connected_spans:
                    unique_connected_spans[span_idx] = (span_row, False, is_start_b)
                else:
                    # Span connects both nodes - mark it
                    unique_connected_spans[span_idx] = (span_row, True, True)

            # When exactly 2 auto-generated nodes are in proximity:
            # If exactly 2 spans are connected, merge them into a single span and remove both nodes
            if len(unique_connected_spans) == 2:
                # Exactly 2 spans: merge them into a single span
                # First, collect span information
                span_list = list(unique_connected_spans.items())
                span1_idx, (span1_row, connects_to_a1, connects_to_b1) = span_list[0]
                span2_idx, (span2_row, connects_to_a2, connects_to_b2) = span_list[1]
                
                span1_geom = span1_row["geometry"]
                span2_geom = span2_row["geometry"]
                
                span1_start_dict = (
                    json.loads(span1_row["start"])
                    if isinstance(span1_row["start"], str) and span1_row["start"] is not None
                    else span1_row["start"]
                )
                span1_end_dict = (
                    json.loads(span1_row["end"])
                    if isinstance(span1_row["end"], str) and span1_row["end"] is not None
                    else span1_row["end"]
                )
                span2_start_dict = (
                    json.loads(span2_row["start"])
                    if isinstance(span2_row["start"], str) and span2_row["start"] is not None
                    else span2_row["start"]
                )
                span2_end_dict = (
                    json.loads(span2_row["end"])
                    if isinstance(span2_row["end"], str) and span2_row["end"] is not None
                    else span2_row["end"]
                )
                
                # Identify which endpoints are connected to the auto-generated nodes
                span1_start_id = span1_start_dict.get("id") if isinstance(span1_start_dict, dict) else None
                span1_end_id = span1_end_dict.get("id") if isinstance(span1_end_dict, dict) else None
                span2_start_id = span2_start_dict.get("id") if isinstance(span2_start_dict, dict) else None
                span2_end_id = span2_end_dict.get("id") if isinstance(span2_end_dict, dict) else None
                
                # Determine which endpoint of span1 is connected to Node A or B
                span1_connected_to_auto = None
                span1_other_end = None
                if span1_start_id == node_a_id or span1_start_id == node_b_id:
                    span1_connected_to_auto = "start"
                    span1_other_end = span1_end_dict
                elif span1_end_id == node_a_id or span1_end_id == node_b_id:
                    span1_connected_to_auto = "end"
                    span1_other_end = span1_start_dict
                
                # Determine which endpoint of span2 is connected to Node A or B
                span2_connected_to_auto = None
                span2_other_end = None
                if span2_start_id == node_a_id or span2_start_id == node_b_id:
                    span2_connected_to_auto = "start"
                    span2_other_end = span2_end_dict
                elif span2_end_id == node_a_id or span2_end_id == node_b_id:
                    span2_connected_to_auto = "end"
                    span2_other_end = span2_start_dict
                
                # Orient spans so the auto-generated node endpoints are connected
                # The merged span should go from span1's other end to span2's other end
                # We need to connect the auto-generated node endpoints together
                if span1_connected_to_auto == "start" and span2_connected_to_auto == "start":
                    # Both connected at start: span1 = A->X, span2 = B->Y
                    # Reverse span1 to get X->A, then join with B->Y to get X->A->B->Y
                    geom1 = LineString(list(span1_geom.coords)[::-1])
                    geom2 = span2_geom
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "start" and span2_connected_to_auto == "end":
                    # Span1 at start (A->X), span2 at end (Y->B)
                    # Join A->X with Y->B to get A->X->Y->B, then reverse to get X->Y->B->A
                    # Actually, we want X->Y, so reverse span1: X->A, reverse span2: B->Y
                    # Join: X->A + B->Y = X->A->B->Y, which becomes X->Y
                    geom1 = LineString(list(span1_geom.coords)[::-1])
                    geom2 = LineString(list(span2_geom.coords)[::-1])
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "end" and span2_connected_to_auto == "start":
                    # Span1 at end (X->A), span2 at start (B->Y)
                    # Join X->A with B->Y to get X->A->B->Y, which becomes X->Y
                    geom1 = span1_geom
                    geom2 = span2_geom
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                elif span1_connected_to_auto == "end" and span2_connected_to_auto == "end":
                    # Both connected at end: span1 = X->A, span2 = Y->B
                    # Reverse span2 to get B->Y, then join with X->A to get X->A->B->Y
                    geom1 = span1_geom
                    geom2 = LineString(list(span2_geom.coords)[::-1])
                    new_start_node = span1_other_end
                    new_end_node = span2_other_end
                else:
                    # Fallback: use closest ends (shouldn't happen if spans are connected to nodes)
                    span1_start_coord = span1_geom.coords[0]
                    span1_end_coord = span1_geom.coords[-1]
                    span2_start_coord = span2_geom.coords[0]
                    span2_end_coord = span2_geom.coords[-1]
                    
                    span1_start_point = Point(span1_start_coord)
                    span1_end_point = Point(span1_end_coord)
                    span2_start_point = Point(span2_start_coord)
                    span2_end_point = Point(span2_end_coord)
                    
                    dist_start1_end2 = span1_start_point.distance(span2_end_point)
                    dist_end1_start2 = span1_end_point.distance(span2_start_point)
                    
                    if dist_start1_end2 <= dist_end1_start2:
                        geom1 = span1_geom
                        geom2 = LineString(list(span2_geom.coords)[::-1])
                        new_start_node = span1_start_dict
                        new_end_node = span2_start_dict
                    else:
                        geom1 = LineString(list(span1_geom.coords)[::-1])
                        geom2 = span2_geom
                        new_start_node = span1_end_dict
                        new_end_node = span2_end_dict
                
                # Join the geometries
                joined_coords = list(geom1.coords) + list(geom2.coords)
                
                # Remove duplicate consecutive coordinates
                cleaned_coords = [joined_coords[0]]
                for coord in joined_coords[1:]:
                    if coord != cleaned_coords[-1]:
                        cleaned_coords.append(coord)
                
                if len(cleaned_coords) >= 2:
                    # Validate that the node references exist in gdf_ofds_nodes
                    valid_start_node = None
                    valid_end_node = None
                    
                    if new_start_node and isinstance(new_start_node, dict):
                        start_node_id = new_start_node.get("id")
                        if start_node_id:
                            # Check if node exists in gdf_ofds_nodes
                            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == start_node_id].empty
                            if node_exists:
                                valid_start_node = new_start_node
                            else:
                                print(
                                    f"WARNING: Phase 5 - Start node ID '{start_node_id}' "
                                    f"not found in nodes GeoDataFrame. Skipping span merge."
                                )
                                continue
                    
                    if new_end_node and isinstance(new_end_node, dict):
                        end_node_id = new_end_node.get("id")
                        if end_node_id:
                            # Check if node exists in gdf_ofds_nodes
                            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == end_node_id].empty
                            if node_exists:
                                valid_end_node = new_end_node
                            else:
                                print(
                                    f"WARNING: Phase 5 - End node ID '{end_node_id}' "
                                    f"not found in nodes GeoDataFrame. Skipping span merge."
                                )
                                continue
                    
                    # Only create the new span if we have valid node references
                    if valid_start_node is None and valid_end_node is None:
                        print(
                            f"WARNING: Phase 5 - Both start and end nodes are invalid. "
                            f"Skipping span merge."
                        )
                        continue
                    
                    joined_geometry = LineString(cleaned_coords)
                    
                    # Create new span
                    new_span = span1_row.copy()
                    new_span["id"] = str(uuid.uuid4())
                    new_span["geometry"] = joined_geometry
                    if valid_start_node:
                        new_span["start"] = json.dumps(
                            convert_to_serializable(valid_start_node)
                        )
                    else:
                        new_span["start"] = None
                    if valid_end_node:
                        new_span["end"] = json.dumps(
                            convert_to_serializable(valid_end_node)
                        )
                    else:
                        new_span["end"] = None
                    
                    new_spans.append(new_span)
                
                # Mark both spans for removal
                if span1_idx not in spans_to_remove:
                    spans_to_remove.append(span1_idx)
                if span2_idx not in spans_to_remove:
                    spans_to_remove.append(span2_idx)

                # Mark both nodes for removal
                nodes_to_remove_phase45.add(node_a_id)
                nodes_to_remove_phase45.add(node_b_id)

        # Remove old spans and add new joined spans
        if spans_to_remove:
            gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove)
            if new_spans:
                new_spans_gdf = gpd.GeoDataFrame(new_spans, crs=gdf_ofds_spans.crs)
                gdf_ofds_spans = pd.concat(
                    [gdf_ofds_spans, new_spans_gdf], ignore_index=True
                )

        # Remove the nodes processed in Phase 5
        if nodes_to_remove_phase45:
            gdf_ofds_nodes = gdf_ofds_nodes[~gdf_ofds_nodes["id"].isin(list(nodes_to_remove_phase45))]
            pairs_processed = len(unique_pairs)
            print(
                f"Phase 5: Processed {pairs_processed} pairs (2 nodes). "
                f"Removed {len(nodes_to_remove_phase45)} auto-generated nodes, "
                f"joined {len(spans_to_remove)} spans into {len(new_spans)} spans. "
                f"Remaining nodes: {len(gdf_ofds_nodes)}"
            )
    
    # Write debug files after Phase 5
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase5", debug_output_prefix)

    # Phase 6: Split Spans at Auto-Generated Nodes
    # For auto-generated nodes that are endpoints of spans and in proximity to another span
    # (but not near an endpoint of that span), extend the span and split the other span
    # to create a network fork with three connected spans
    
    # Get all auto-generated nodes that are endpoints of spans
    start_ids = gdf_ofds_spans["start"].apply(extract_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())
    
    auto_gen_endpoint_nodes = gdf_ofds_nodes[
        (gdf_ofds_nodes["name"] == "Auto generated missing node") &
        (gdf_ofds_nodes["id"].isin(span_endpoint_ids))
    ]
    
    spans_to_remove = []
    new_spans = []
    nodes_to_rename = {}
    node_processing_status = {}
    
    for node_idx, node_row in auto_gen_endpoint_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        
        # Find the span where this node is an endpoint
        node_span_idx = None
        node_is_start = False
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove:
                continue
            span_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            span_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )
            if isinstance(span_start, dict) and span_start.get("id") == node_id:
                node_span_idx = span_idx
                node_is_start = True
                break
            elif isinstance(span_end, dict) and span_end.get("id") == node_id:
                node_span_idx = span_idx
                node_is_start = False
                break
        
        if node_span_idx is None:
            node_processing_status[node_id] = "SKIPPED: Not found as endpoint of any span"
            continue
        
        # Find nearest span (excluding the span where this node is an endpoint)
        min_span_distance = float("inf")
        nearest_span_idx = None
        nearest_point_on_span = None
        
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove or span_idx == node_span_idx:
                continue
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_idx = span_idx
                nearest_point_on_span = nearest_point
        
        # Check if within threshold
        if min_span_distance == float("inf") or min_span_distance > threshold:
            dist_str = f"{(min_span_distance / METERS_TO_DEGREES):.2f}" if min_span_distance != float("inf") else "inf"
            node_processing_status[node_id] = f"SKIPPED: Too far ({dist_str}m > {threshold_meters}m)"
            continue
        
        if nearest_span_idx is None or nearest_point_on_span is None:
            node_processing_status[node_id] = "SKIPPED: Could not find nearest span"
            continue
        
        # Check if nearest point is at an endpoint of the span (skip if so)
        target_span_row = gdf_ofds_spans.loc[nearest_span_idx]
        target_span_line = target_span_row.geometry
        target_start_point = Point(target_span_line.coords[0])
        target_end_point = Point(target_span_line.coords[-1])
        endpoint_tolerance = 1e-6
        
        if (
            nearest_point_on_span.distance(target_start_point) < endpoint_tolerance
            or nearest_point_on_span.distance(target_end_point) < endpoint_tolerance
        ):
            node_processing_status[node_id] = "SKIPPED: Nearest point is at span endpoint"
            continue
        
        # Process: extend node's span, split target span, create fork
        # 1. Extend the span that has the auto-generated node to the fork point
        node_span_row = gdf_ofds_spans.loc[node_span_idx]
        node_span_geom = node_span_row.geometry
        node_span_start = (
            json.loads(node_span_row["start"])
            if isinstance(node_span_row["start"], str)
            else node_span_row["start"]
        )
        node_span_end = (
            json.loads(node_span_row["end"])
            if isinstance(node_span_row["end"], str)
            else node_span_row["end"]
        )
        
        # Create fork node info
        fork_node_info = {
            "id": node_id,
            "name": "network fork",
            "location": {
                "type": "Point",
                "coordinates": [nearest_point_on_span.x, nearest_point_on_span.y],
            },
        }
        
        # Extend the node's span to the fork point
        node_span_coords = list(node_span_geom.coords)
        fork_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
        
        if node_is_start:
            # Node is at start, extend from start
            if Point(node_span_coords[0]).distance(nearest_point_on_span) > 1e-9:
                node_span_coords.insert(0, fork_coord)
            extended_node_span_start = fork_node_info
            extended_node_span_end = node_span_end.copy() if isinstance(node_span_end, dict) else node_span_end
        else:
            # Node is at end, extend from end
            if Point(node_span_coords[-1]).distance(nearest_point_on_span) > 1e-9:
                node_span_coords.append(fork_coord)
            extended_node_span_start = node_span_start.copy() if isinstance(node_span_start, dict) else node_span_start
            extended_node_span_end = fork_node_info
        
        # Create extended span
        extended_span = node_span_row.copy()
        extended_span["geometry"] = LineString(node_span_coords)
        extended_span["start"] = json.dumps(convert_to_serializable(extended_node_span_start))
        extended_span["end"] = json.dumps(convert_to_serializable(extended_node_span_end))
        new_spans.append(extended_span)
        spans_to_remove.append(node_span_idx)
        
        # 2. Split the target span at the fork point
        target_coords = list(target_span_line.coords)
        split_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
        
        # Find where to insert the split point by finding the closest segment
        insert_index = None
        min_dist_to_segment = float("inf")
        
        for i in range(len(target_coords) - 1):
            seg_line = LineString([target_coords[i], target_coords[i + 1]])
            dist_to_seg = nearest_point_on_span.distance(seg_line)
            if dist_to_seg < min_dist_to_segment:
                min_dist_to_segment = dist_to_seg
                # Determine insertion point based on which end of segment is closer
                seg_start_pt = Point(target_coords[i])
                seg_end_pt = Point(target_coords[i + 1])
                dist_to_start = nearest_point_on_span.distance(seg_start_pt)
                dist_to_end = nearest_point_on_span.distance(seg_end_pt)
                
                if dist_to_start < 1e-6:
                    # Point is at start of segment
                    insert_index = i
                elif dist_to_end < 1e-6:
                    # Point is at end of segment
                    insert_index = i + 1
                else:
                    # Point is somewhere in the middle of the segment
                    insert_index = i + 1
        
        if insert_index is None:
            node_processing_status[node_id] = "SKIPPED: Could not determine split location"
            continue
        
        # Check if point already exists at this index (within tolerance)
        point_exists = False
        if insert_index < len(target_coords):
            existing_pt = Point(target_coords[insert_index])
            if existing_pt.distance(nearest_point_on_span) < 1e-6:
                point_exists = True
        
        if not point_exists:
            target_coords.insert(insert_index, split_coord)
        
        # Create two segments
        segment1_coords = target_coords[:insert_index + 1]
        segment2_coords = target_coords[insert_index:]
        
        if len(segment1_coords) < 2 or len(segment2_coords) < 2:
            node_processing_status[node_id] = "SKIPPED: Insufficient points for segments"
            continue
        
        segment1 = LineString(segment1_coords)
        segment2 = LineString(segment2_coords)
        
        # Get target span endpoints
        target_start = (
            json.loads(target_span_row["start"])
            if isinstance(target_span_row["start"], str)
            else target_span_row["start"]
        )
        target_end = (
            json.loads(target_span_row["end"])
            if isinstance(target_span_row["end"], str)
            else target_span_row["end"]
        )
        
        # Determine which segment connects to which endpoint
        seg1_start_pt = Point(segment1.coords[0])
        seg1_end_pt = Point(segment1.coords[-1])
        target_start_pt = Point(target_start["location"]["coordinates"]) if target_start else None
        
        if target_start_pt and seg1_start_pt.distance(target_start_pt) < 1e-3:
            # Segment1 connects to start
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment1
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment2
        elif target_start_pt and seg1_end_pt.distance(target_start_pt) < 1e-3:
            # Segment1 reversed connects to start
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment2
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment1
        else:
            # Segment1 connects to end
            new_span1_start = target_start
            new_span1_end = fork_node_info
            new_span1_geom = segment2
            new_span2_start = fork_node_info
            new_span2_end = target_end
            new_span2_geom = segment1
        
        # Create two new spans from the split
        split_span1 = target_span_row.copy()
        split_span1["id"] = str(uuid.uuid4())
        split_span1["geometry"] = new_span1_geom
        split_span1["start"] = json.dumps(convert_to_serializable(new_span1_start))
        split_span1["end"] = json.dumps(convert_to_serializable(new_span1_end))
        
        split_span2 = target_span_row.copy()
        split_span2["id"] = str(uuid.uuid4())
        split_span2["geometry"] = new_span2_geom
        split_span2["start"] = json.dumps(convert_to_serializable(new_span2_start))
        split_span2["end"] = json.dumps(convert_to_serializable(new_span2_end))
        
        new_spans.append(split_span1)
        new_spans.append(split_span2)
        spans_to_remove.append(nearest_span_idx)
        
        # Mark node for renaming and moving
        nodes_to_rename[node_id] = nearest_point_on_span
        dist_str = f"{(min_span_distance / METERS_TO_DEGREES):.2f}"
        node_processing_status[node_id] = f"PROCESSED: Created fork ({dist_str}m from span {nearest_span_idx})"

    # Remove old spans and add new ones
    if spans_to_remove:
        gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove)
        if new_spans:
            new_spans_gdf = gpd.GeoDataFrame(new_spans, crs=gdf_ofds_spans.crs)
            gdf_ofds_spans = pd.concat(
                [gdf_ofds_spans, new_spans_gdf], ignore_index=True
            )

    # Update node geometries and names
    for node_id, new_geometry in nodes_to_rename.items():
        node_matches = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id]
        if len(node_matches) == 0:
            print(f"  [WARNING] Phase 6: Node {node_id[:8]}... not found for renaming")
            continue
        node_idx = node_matches.index[0]
        old_name = gdf_ofds_nodes.at[node_idx, "name"]
        gdf_ofds_nodes.at[node_idx, "geometry"] = new_geometry
        gdf_ofds_nodes.at[node_idx, "name"] = "network fork"
        print(f"  [DEBUG] Phase 6: Renamed node {node_id[:8]}... from '{old_name}' to 'network fork'")

    # Add processing status field to nodes for debugging
    if "phase6_status" not in gdf_ofds_nodes.columns:
        gdf_ofds_nodes["phase6_status"] = "Not processed"
    
    for node_id, status in node_processing_status.items():
        node_idx = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].index
        if len(node_idx) > 0:
            gdf_ofds_nodes.at[node_idx[0], "phase6_status"] = status
    
    # Print summary of processing status
    processed_count = sum(1 for s in node_processing_status.values() if s.startswith("PROCESSED"))
    skipped_count = len(node_processing_status) - processed_count
    total_nodes = len(auto_gen_endpoint_nodes)
    unprocessed_count = total_nodes - len(node_processing_status)
    
    print(f"Phase 6: Processed {processed_count} nodes, skipped {skipped_count} nodes, {unprocessed_count} nodes not evaluated")
    
    if nodes_to_rename:
        print(
            f"Phase 6: Moved and renamed {len(nodes_to_rename)} nodes to fork points, "
            f"split {len(spans_to_remove)} spans"
        )
    
    # Print details for skipped nodes (for debugging)
    if skipped_count > 0:
        print("  Phase 6 skipped nodes:")
        for node_id, status in node_processing_status.items():
            if status.startswith("SKIPPED"):
                node_idx = gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].index
                if len(node_idx) > 0:
                    node_name = gdf_ofds_nodes.at[node_idx[0], "name"]
                    print(f"    Node {node_id[:8]}... ({node_name}): {status}")
    
    # Write debug files after Phase 6
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase6", debug_output_prefix)

    # Phase 7: Split Spans at Proper Nodes
    # Process proper nodes that are near spans (but not endpoints)
    # Split spans at the nearest point and connect to the proper node's location
    start_ids = gdf_ofds_spans["start"].apply(extract_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_id)
    span_endpoint_ids = set(pd.concat([start_ids, end_ids]).dropna())

    # Process proper nodes (not auto-generated) that are not already span endpoints
    proper_nodes = gdf_ofds_nodes[
        (gdf_ofds_nodes["name"] != "Auto generated missing node") &
        (gdf_ofds_nodes["name"] != "network fork") &
        (~gdf_ofds_nodes["id"].isin(span_endpoint_ids))
    ]

    spans_to_remove_proper = []
    new_spans_proper = []

    for node_idx, node_row in proper_nodes.iterrows():
        node_point = node_row.geometry
        node_id = node_row["id"]
        node_name = node_row["name"]

        # Find spans where this node is currently an endpoint
        current_span_ids = []
        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove_proper:
                continue
            span_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            span_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )
            if (isinstance(span_start, dict) and span_start.get("id") == node_id) or (
                isinstance(span_end, dict) and span_end.get("id") == node_id
            ):
                current_span_ids.append(span_idx)

        # Find nearest span (excluding spans where this node is already an endpoint)
        min_span_distance = float("inf")
        nearest_span_idx = None
        nearest_point_on_span = None

        for span_idx, span_row in gdf_ofds_spans.iterrows():
            if span_idx in spans_to_remove_proper or span_idx in current_span_ids:
                continue
            span_line = span_row.geometry
            nearest_point = nearest_points(node_point, span_line)[1]
            distance = node_point.distance(nearest_point)
            if distance < min_span_distance:
                min_span_distance = distance
                nearest_span_idx = span_idx
                nearest_point_on_span = nearest_point

        # If within threshold, split span and connect to proper node
        if (
            min_span_distance <= threshold
            and nearest_span_idx is not None
            and nearest_point_on_span is not None
        ):
            span_row = gdf_ofds_spans.loc[nearest_span_idx]
            span_line = span_row.geometry

            # Check that nearest point is not at span endpoints
            start_point = Point(span_line.coords[0])
            end_point = Point(span_line.coords[-1])
            endpoint_tolerance = 1e-6
            if (
                nearest_point_on_span.distance(start_point) < endpoint_tolerance
                or nearest_point_on_span.distance(end_point) < endpoint_tolerance
            ):
                continue

            # Insert the split point into the LineString coordinates
            coords = list(span_line.coords)
            split_coord = (nearest_point_on_span.x, nearest_point_on_span.y)
            
            # Find the index where to insert the split point
            insert_index = None
            min_dist_to_segment = float("inf")
            
            for i in range(len(coords) - 1):
                seg_start = Point(coords[i])
                seg_end = Point(coords[i + 1])
                seg_line = LineString([coords[i], coords[i + 1]])
                
                dist_to_seg = nearest_point_on_span.distance(seg_line)
                if dist_to_seg < min_dist_to_segment:
                    min_dist_to_segment = dist_to_seg
                    dist_to_start = nearest_point_on_span.distance(seg_start)
                    dist_to_end = nearest_point_on_span.distance(seg_end)
                    
                    if dist_to_start < 1e-9:
                        insert_index = i + 1
                        break
                    elif dist_to_end < 1e-9:
                        insert_index = i + 1
                        break
                    else:
                        insert_index = i + 1
            
            # Insert the split point into coordinates
            if insert_index is not None:
                point_already_exists = False
                for coord in coords:
                    if Point(coord).distance(nearest_point_on_span) < 1e-9:
                        point_already_exists = True
                        insert_index = coords.index(coord)
                        break
                
                if not point_already_exists:
                    coords.insert(insert_index, split_coord)
            
            # Create two segments from the coordinates
            if insert_index is None or len(coords) < 3:
                continue
            
            # Split at the inserted point
            segment1_coords = coords[:insert_index + 1]
            segment2_coords = coords[insert_index:]
            
            # Ensure segments have at least 2 points
            if len(segment1_coords) < 2 or len(segment2_coords) < 2:
                continue
            
            segment1 = LineString(segment1_coords)
            segment2 = LineString(segment2_coords)

            # Get original span's start and end node info
            original_start = (
                json.loads(span_row["start"])
                if isinstance(span_row["start"], str)
                else span_row["start"]
            )
            original_end = (
                json.loads(span_row["end"])
                if isinstance(span_row["end"], str)
                else span_row["end"]
            )

            # Extend segments to the proper node's actual location
            seg1_end_point = Point(segment1.coords[-1])
            if seg1_end_point.distance(node_point) > 1e-9:
                segment1_coords_extended = list(segment1.coords) + [(node_point.x, node_point.y)]
                segment1 = LineString(segment1_coords_extended)
            
            seg2_start_point = Point(segment2.coords[0])
            if seg2_start_point.distance(node_point) > 1e-9:
                segment2_coords_extended = [(node_point.x, node_point.y)] + list(segment2.coords)
                segment2 = LineString(segment2_coords_extended)

            # Determine which segment connects to which endpoint
            seg1_start = Point(segment1.coords[0])
            seg1_end = Point(segment1.coords[-1])
            seg2_start = Point(segment2.coords[0])

            original_start_point = (
                Point(original_start["location"]["coordinates"])
                if original_start
                else None
            )
            original_end_point = (
                Point(original_end["location"]["coordinates"])
                if original_end
                else None
            )

            # Verify the node still exists in gdf_ofds_nodes before creating reference
            node_exists = not gdf_ofds_nodes[gdf_ofds_nodes["id"] == node_id].empty
            if not node_exists:
                print(
                    f"WARNING: Node ID '{node_id}' (name: '{node_name}') "
                    f"not found in nodes GeoDataFrame. Skipping span split."
                )
                continue
            
            # Create node info for the proper node
            proper_node_info = {
                "id": node_id,
                "name": node_name,
                "location": {
                    "type": "Point",
                    "coordinates": [
                        node_point.x,
                        node_point.y,
                    ],
                },
            }

            # Determine segment assignments
            if (
                original_start_point
                and seg1_start.distance(original_start_point) < 1e-3
            ):
                new_span1_start = original_start
                new_span1_end = proper_node_info
                new_span1_geom = segment1
                new_span2_start = proper_node_info
                new_span2_end = original_end
                new_span2_geom = segment2
            elif (
                original_start_point
                and seg2_start.distance(original_start_point) < 1e-3
            ):
                new_span1_start = original_start
                new_span1_end = proper_node_info
                new_span1_geom = segment2
                new_span2_start = proper_node_info
                new_span2_end = original_end
                new_span2_geom = segment1
            else:
                seg1_matches_end = (
                    original_end_point
                    and seg1_end.distance(original_end_point) < 1e-3
                )
                if seg1_matches_end:
                    new_span1_start = original_start
                    new_span1_end = proper_node_info
                    new_span1_geom = segment2
                    new_span2_start = proper_node_info
                    new_span2_end = original_end
                    new_span2_geom = segment1
                else:
                    new_span1_start = original_start
                    new_span1_end = proper_node_info
                    new_span1_geom = segment1
                    new_span2_start = proper_node_info
                    new_span2_end = original_end
                    new_span2_geom = segment2

            # Create new span records
            new_span1 = span_row.copy()
            new_span1["id"] = str(uuid.uuid4())
            new_span1["geometry"] = new_span1_geom
            new_span1["start"] = json.dumps(
                convert_to_serializable(new_span1_start)
            )
            new_span1["end"] = json.dumps(
                convert_to_serializable(new_span1_end)
            )

            new_span2 = span_row.copy()
            new_span2["id"] = str(uuid.uuid4())
            new_span2["geometry"] = new_span2_geom
            new_span2["start"] = json.dumps(
                convert_to_serializable(new_span2_start)
            )
            new_span2["end"] = json.dumps(
                convert_to_serializable(new_span2_end)
            )

            new_spans_proper.append(new_span1)
            new_spans_proper.append(new_span2)
            spans_to_remove_proper.append(nearest_span_idx)

    # Remove old spans and add new ones
    if spans_to_remove_proper:
        gdf_ofds_spans = gdf_ofds_spans.drop(index=spans_to_remove_proper)
        if new_spans_proper:
            new_spans_gdf = gpd.GeoDataFrame(new_spans_proper, crs=gdf_ofds_spans.crs)
            gdf_ofds_spans = pd.concat(
                [gdf_ofds_spans, new_spans_gdf], ignore_index=True
            )

    if spans_to_remove_proper:
        print(
            f"Phase 7: Processed {len(proper_nodes)} proper nodes near spans, "
            f"split {len(spans_to_remove_proper)} spans"
        )
    else:
        print(f"Phase 7: Processed {len(proper_nodes)} proper nodes near spans, no spans split")
    
    # Write debug files after Phase 7
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase7", debug_output_prefix)

    # Phase 8: Remove Duplicate Spans
    print("\nPhase 8: Removing bidirectional duplicate spans...")
    spans_before_dedup = len(gdf_ofds_spans)
    
    # Create a normalized representation: (min(start_id, end_id), max(start_id, end_id))
    # This treats A->B and B->A as the same connection
    normalized_connections = {}
    spans_to_keep = []
    
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_id = None
        end_id = None
        
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
        
        # Skip spans without both start and end nodes
        if start_id is None or end_id is None:
            spans_to_keep.append(index)
            continue
        
        # Create normalized connection key (sorted tuple to treat A->B and B->A as same)
        connection_key = tuple(sorted([start_id, end_id]))
        
        # Check if we've seen this connection before
        if connection_key not in normalized_connections:
            # First time seeing this connection - keep it
            normalized_connections[connection_key] = index
            spans_to_keep.append(index)
        else:
            # Duplicate connection (A->B vs B->A) - check if geometries are the same or reversed
            existing_index = normalized_connections[connection_key]
            existing_span = gdf_ofds_spans.loc[existing_index]
            existing_geom = existing_span["geometry"]
            current_geom = span["geometry"]
            
            # Check if geometries are the same or reversed
            coords_match = (
                list(existing_geom.coords) == list(current_geom.coords)
                or list(existing_geom.coords) == list(current_geom.coords)[::-1]
            )
            
            if coords_match:
                # Duplicate span (same connection, same geometry) - skip it
                continue
            else:
                # Same nodes but different geometry - this might be intentional (different paths)
                # For now, keep the first one and skip duplicates to avoid bidirectional duplicates
                # If you need to keep multiple paths between the same nodes, this can be adjusted
                continue
    
    # Filter to keep only non-duplicate spans
    gdf_ofds_spans = gdf_ofds_spans.loc[spans_to_keep].copy()
    spans_removed = spans_before_dedup - len(gdf_ofds_spans)
    
    if spans_removed > 0:
        print(f"Phase 8: Removed {spans_removed} bidirectional duplicate spans")
    else:
        print("Phase 8: No bidirectional duplicates found")
    
    # Write debug files after Phase 8
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase8", debug_output_prefix)

    # Phase 9: Rename Spans from Nodes
    if rename_spans_from_nodes:
        print("\nPhase 9: Renaming spans from node names...")
        spans_renamed = 0
        for index, span in gdf_ofds_spans.iterrows():
            start_dict = (
                json.loads(span["start"])
                if isinstance(span["start"], str) and span["start"] is not None
                else span["start"]
            )
            end_dict = (
                json.loads(span["end"])
                if isinstance(span["end"], str) and span["end"] is not None
                else span["end"]
            )
            
            start_name = None
            end_name = None
            
            if start_dict is not None and isinstance(start_dict, dict):
                start_name = start_dict.get("name")
            
            if end_dict is not None and isinstance(end_dict, dict):
                end_name = end_dict.get("name")
            
            # Create new name: "start name - end name"
            if start_name and end_name:
                new_name = f"{start_name} - {end_name}"
                gdf_ofds_spans.at[index, "name"] = new_name
                spans_renamed += 1
            elif start_name:
                # Only start node has a name
                gdf_ofds_spans.at[index, "name"] = start_name
                spans_renamed += 1
            elif end_name:
                # Only end node has a name
                gdf_ofds_spans.at[index, "name"] = end_name
                spans_renamed += 1
            # If neither has a name, leave the span name unchanged
        
        print(f"Phase 9: Renamed {spans_renamed} spans from node names")
        
        # Write debug files after Phase 9
        if debug_enabled:
            write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase9", debug_output_prefix)
    
    # Phase 10: Validate node references
    print("\nPhase 10: Validating node references in spans...")
    valid_node_ids = set(gdf_ofds_nodes["id"].unique())
    invalid_spans = []
    
    for index, span in gdf_ofds_spans.iterrows():
        start_dict = (
            json.loads(span["start"])
            if isinstance(span["start"], str) and span["start"] is not None
            else span["start"]
        )
        end_dict = (
            json.loads(span["end"])
            if isinstance(span["end"], str) and span["end"] is not None
            else span["end"]
        )
        
        start_id = None
        end_id = None
        
        if start_dict is not None and isinstance(start_dict, dict):
            start_id = start_dict.get("id")
        if end_dict is not None and isinstance(end_dict, dict):
            end_id = end_dict.get("id")
        
        issues = []
        if start_id is not None and start_id not in valid_node_ids:
            issues.append(f"start node ID '{start_id}' not found")
        if end_id is not None and end_id not in valid_node_ids:
            issues.append(f"end node ID '{end_id}' not found")
        
        if issues:
            invalid_spans.append({
                "span_id": span.get("id", f"span_{index}"),
                "span_name": span.get("name", "unknown"),
                "issues": issues,
                "start_id": start_id,
                "end_id": end_id
            })
    
    if invalid_spans:
        print(f"WARNING: Found {len(invalid_spans)} spans with invalid node references:")
        for invalid in invalid_spans[:10]:  # Show first 10
            print(f"  - Span '{invalid['span_name']}' (ID: {invalid['span_id']}): {', '.join(invalid['issues'])}")
        if len(invalid_spans) > 10:
            print(f"  ... and {len(invalid_spans) - 10} more")
        print("\nThese spans reference nodes that don't exist in the nodes array.")
        print("This can happen when:")
        print("  1. Nodes are removed during consolidation but spans aren't updated")
        print("  2. Spans are created with node references before nodes are finalized")
        print("  3. Node ID lookups fail silently during merge operations")
    else:
        print("Phase 10: All spans have valid node references")
    
    # Phase 11: Final Summary
    print(f"\nFinal counts: {len(gdf_ofds_spans)} spans, {len(gdf_ofds_nodes)} nodes")
    
    # Write debug files after Phase 11 (final)
    if debug_enabled:
        write_debug_geojson(gdf_ofds_nodes, gdf_ofds_spans, debug_output_dir, "phase11", debug_output_prefix)

    return gdf_ofds_spans, gdf_ofds_nodes


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


def append_node(
    new_node_coords,
    network_id,
    network_name,
    network_links,
    physical_infrastructure_provider_id,
    physical_infrastructure_provider_name,
    network_providers_id,
    network_providers_name,
):
    # Returns a GeoJSON feature dictionary representing the new node
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": new_node_coords},
        "properties": {
            "id": str(uuid.uuid4()),  # Generate a new UUID for the id
            "name": "Auto generated missing node",
            "network": {"id": network_id, "name": network_name, "links": network_links},
            "physicalInfrastructureProvider": {
                "id": physical_infrastructure_provider_id,
                "name": physical_infrastructure_provider_name,
            },
            "networkProviders": [
                {
                    "id": network_providers_id,
                    "name": network_providers_name,
                }
            ],
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

    # Define defaults for all configuration variables
    defaults = {
        "kml_file_name": None,  # Required, will check separately
        "network_name": "Default Network Name",
        "network_id": str(uuid.uuid4()),
        "network_links": "https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json",
        "physicalInfrastructureProvider_name": "",
        "physicalInfrastructureProvider_id": "",
        "networkProviders_name": "",
        "networkProviders_id": "",
        "ignore_placemarks": "",
        "input_directory": "input/",
        "output_directory": "output/",
        "output_name_prefix": "",
        "threshold_meters": "5000",
        "debug_enabled": "false",
        "rename_spans_from_nodes": "false",
        "merge_contiguous_spans": "false",
        "merge_contiguous_spans_precision": "6",
    }

    # Extract all variables with defaults in one go
    kml_file = network_prof.get("kml_file_name") or defaults["kml_file_name"]
    if not kml_file:
        print("Error. Please set kml file name in network profile")
        sys.exit(1)

    network_name = network_prof.get("network_name") or defaults["network_name"]
    if network_name == defaults["network_name"]:
        print("Network name not found in config file. Using default value.")

    network_id = network_prof.get("network_id") or defaults["network_id"]

    network_link_url = network_prof.get("network_links") or defaults["network_links"]
    if network_link_url == defaults["network_links"]:
        print("Network links not found in config file. Using default value.")

    network_links = [{"rel": "describedby", "href": network_link_url}]

    # Extract provider information
    physical_infrastructure_provider_name = network_prof.get(
        "physicalInfrastructureProvider_name",
        defaults["physicalInfrastructureProvider_name"],
    )
    physical_infrastructure_provider_id = network_prof.get(
        "physicalInfrastructureProvider_id",
        defaults["physicalInfrastructureProvider_id"],
    )
    network_providers_name = network_prof.get(
        "networkProviders_name", defaults["networkProviders_name"]
    )
    network_providers_id = network_prof.get(
        "networkProviders_id", defaults["networkProviders_id"]
    )
    # Handle ignore_placemarks (split by semicolon if present)
    ignore_placemarks_str = network_prof.get(
        "ignore_placemarks", defaults["ignore_placemarks"]
    )
    ignore_placemarks = (
        ignore_placemarks_str.split(";") if ignore_placemarks_str else []
    )

    # Extract threshold_meters for consolidate_auto_generated_nodes
    threshold_meters_str = network_prof.get("threshold_meters", defaults["threshold_meters"])
    try:
        threshold_meters = float(threshold_meters_str)
    except (ValueError, TypeError):
        print(f"Warning: Invalid threshold_meters value '{threshold_meters_str}'. Using default 5000.")
        threshold_meters = 5000.0
    
    # Extract debug_enabled flag
    debug_enabled_str = network_prof.get("debug_enabled", defaults["debug_enabled"]).lower()
    debug_enabled = debug_enabled_str in ("true", "1", "yes", "on")
    print(f"Debug mode: {debug_enabled} (read from profile as '{network_prof.get('debug_enabled', 'not found')}')")
    
    # Extract rename_spans_from_nodes flag
    rename_spans_from_nodes_str = network_prof.get(
        "rename_spans_from_nodes", defaults["rename_spans_from_nodes"]
    ).lower()
    rename_spans_from_nodes = rename_spans_from_nodes_str in ("true", "1", "yes", "on")

    # Extract directory settings
    input_directory = network_prof.get("input_directory", defaults["input_directory"])
    output_directory = network_prof.get(
        "output_directory", defaults["output_directory"]
    )
    # Use debug_output_directory if specified, otherwise use output_directory
    debug_output_directory = network_prof.get(
        "debug_output_directory", output_directory
    )
    print(f"Debug output directory: {debug_output_directory} (from profile: '{network_prof.get('debug_output_directory', 'not found')}')")

    # Check if directories exist, if not, create them
    if not os.path.exists(input_directory):
        os.makedirs(input_directory)
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)
    if debug_enabled and not os.path.exists(debug_output_directory):
        os.makedirs(debug_output_directory)
        print(f"Created debug output directory: {debug_output_directory}")

    directory = os.path.join(os.getcwd(), input_directory)
    kml_fullpath = os.path.join(directory, kml_file)

    # Check if KML file exists
    if not os.path.exists(kml_fullpath):
        print(f"\nERROR: KML file not found!")
        print(f"  Expected file: {kml_fullpath}")
        print(f"  Profile setting: kml_file_name = {kml_file}")
        print(f"  Input directory: {directory}")
        print(f"  Current working directory: {os.getcwd()}")
        if not os.path.exists(directory):
            print(f"  NOTE: Input directory does not exist: {directory}")
        else:
            print(f"  NOTE: Input directory exists but file not found")
            # List files in directory to help user
            try:
                files_in_dir = os.listdir(directory)
                if files_in_dir:
                    print(f"  Files in input directory:")
                    for f in sorted(files_in_dir)[:10]:  # Show first 10 files
                        print(f"    - {f}")
                    if len(files_in_dir) > 10:
                        print(f"    ... and {len(files_in_dir) - 10} more files")
                else:
                    print(f"  Input directory is empty")
            except PermissionError:
                print(f"  Could not list files in input directory (permission denied)")
        sys.exit(1)

    # Set output_name_prefix (use filename-based default if not provided)
    network_filename_normalised = kml_file.replace(" ", "_").upper()
    output_name_prefix = (
        network_prof.get("output_name_prefix") or network_filename_normalised[3:]
    )

    # output files
    today = datetime.today()
    date_string = today.strftime("%d%b%Y").lower()

    nodes_ofds_output = (
        output_directory
        + output_name_prefix
        + "_ofds-nodes_"
        + date_string
        + ".geojson"
    )

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
        kml_fullpath,
        network_id,
        network_name,
        ignore_placemarks,
        physical_infrastructure_provider_id,
        physical_infrastructure_provider_name,
        network_providers_id,
        network_providers_name,
    )

    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(
        f"Breaking spans at node points. \nBefore: {len(gdf_spans)} spans, "
        f"{len(gdf_ofds_nodes)} nodes, min vertices: {min_vert}"
    )
    # Filter out ignored nodes before breaking spans
    gdf_nodes_for_breaking = filter_ignored_nodes(gdf_ofds_nodes, ignore_placemarks)
    print(f"Filtered out ignored nodes: {len(gdf_ofds_nodes)} -> {len(gdf_nodes_for_breaking)} nodes")
    gdf_spans = break_spans_at_node_points(
        gdf_nodes_for_breaking, gdf_spans, network_name, network_id, network_links
    )
    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(
        f" After: {len(gdf_spans)} spans, "
        f"{len(gdf_ofds_nodes)} nodes, min vertices: {min_vert}\n"
    )

    # Optionally merge contiguous spans before adding missing nodes
    merge_contiguous = network_prof.get("merge_contiguous_spans", defaults["merge_contiguous_spans"]).lower() == "true"
    if merge_contiguous:
        merge_precision = int(network_prof.get("merge_contiguous_spans_precision", defaults["merge_contiguous_spans_precision"]))
        spans_before_merge = len(gdf_spans)
        print(f"\nMerging contiguous spans (precision: {merge_precision} decimal places)...")
        print(f"Before merge: {spans_before_merge} spans")
        gdf_spans = merge_contiguous_spans(gdf_spans, precision=merge_precision)
        spans_after_merge = len(gdf_spans)
        spans_merged = spans_before_merge - spans_after_merge
        print(f"After merge: {spans_after_merge} spans ({spans_merged} spans merged)\n")
    
    # Check for any spans that do not have a node at the start or end point and add as needed
    nodes_before = len(gdf_ofds_nodes)
    gdf_ofds_nodes, gdf_auto_gen_nodes = add_missing_nodes(
        gdf_spans,
        gdf_ofds_nodes,
        network_id,
        network_name,
        network_links,
        physical_infrastructure_provider_id,
        physical_infrastructure_provider_name,
        network_providers_id,
        network_providers_name,
    )
    nodes_added = len(gdf_auto_gen_nodes)
    print(
        f"Added {nodes_added} missing nodes. "
        f"Total nodes: {nodes_before} -> {len(gdf_ofds_nodes)}"
    )
    # Add information on the start and end nodes to the spans
    min_vert = pd.Series([len(x.coords) for x in gdf_spans.geometry]).min()
    print(
        f"Adding nodes to spans. \nBefore: {len(gdf_spans)} spans, "
        f"{len(gdf_ofds_nodes)} nodes, min vertices: {min_vert}"
    )
    gdf_ofds_spans = add_nodes_to_spans(gdf_spans, gdf_ofds_nodes)

    # Filter out spans with identical start and end nodes
    spans_before_filter = len(gdf_ofds_spans)
    start_ids = gdf_ofds_spans["start"].apply(extract_node_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_node_id)
    # Keep spans where start and end IDs are different, or where either is None
    valid_mask = (start_ids != end_ids) | (start_ids.isna()) | (end_ids.isna())
    gdf_ofds_spans = gdf_ofds_spans[valid_mask].copy()
    spans_removed = spans_before_filter - len(gdf_ofds_spans)
    if spans_removed > 0:
        print(f"Removed {spans_removed} spans with identical start and end nodes")

    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(
        f" After: {len(gdf_ofds_spans)} spans, "
        f"{len(gdf_ofds_nodes)} nodes, min vertices: {min_vert}\n"
    )

    # Consolidate auto-generated nodes: analyze, merge, and split spans
    spans_before = len(gdf_ofds_spans)
    nodes_before = len(gdf_ofds_nodes)
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(
        f"Consolidating auto-generated nodes. \nBefore: {spans_before} spans, "
        f"{nodes_before} nodes, min vertices: {min_vert}"
    )
    # Use threshold_meters from profile (default: 5000 meters)
    gdf_ofds_spans, gdf_ofds_nodes = consolidate_auto_generated_nodes(
        gdf_ofds_nodes,
        gdf_ofds_spans,
        threshold_meters,
        debug_enabled=debug_enabled,
        debug_output_dir=debug_output_directory,
        debug_output_prefix=output_name_prefix,
        rename_spans_from_nodes=rename_spans_from_nodes,
    )
    spans_after = len(gdf_ofds_spans)
    nodes_after = len(gdf_ofds_nodes)
    min_vert = pd.Series([len(x.coords) for x in gdf_ofds_spans.geometry]).min()
    print(
        f" After: {spans_after} spans ({spans_after - spans_before} net change), "
        f"{nodes_after} nodes ({nodes_before - nodes_after} removed), "
        f"min vertices: {min_vert}\n"
    )

    # Filter out any spans with identical start and end nodes after consolidation
    spans_before_final_filter = len(gdf_ofds_spans)
    start_ids = gdf_ofds_spans["start"].apply(extract_node_id)
    end_ids = gdf_ofds_spans["end"].apply(extract_node_id)
    valid_mask = (start_ids != end_ids) | (start_ids.isna()) | (end_ids.isna())
    gdf_ofds_spans = gdf_ofds_spans[valid_mask].copy()
    spans_removed_final = spans_before_final_filter - len(gdf_ofds_spans)
    if spans_removed_final > 0:
        print(f"Removed {spans_removed_final} additional spans with identical start and end nodes after consolidation")

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
        main(["--help"])
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
