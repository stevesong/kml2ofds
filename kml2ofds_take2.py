import geopandas as gpd
from shapely.ops import nearest_points, split, snap
from shapely.geometry import MultiPoint, MultiLineString, LineString, Point, GeometryCollection


# Load the GeoJSON files into GeoDataFrames
points_gdf = gpd.read_file("output/points.geojson")
lines_gdf = gpd.read_file("output/polylines.geojson")

# Ensure CRS match, if not, reproject
if points_gdf.crs != lines_gdf.crs:
    points_gdf = points_gdf.to_crs(lines_gdf.crs)

# Function to find the nearest line to each point and snap the point to the line
def snap_to_line(point, lines):
    nearest_line = None
    min_distance = float('inf')
    nearest_point_on_line = None

    # Iterate over all lines to find the nearest one
    for line in lines.geometry:
        # Use nearest_points to get the nearest point on the line to our point
        point_on_line = nearest_points(point, line)[1]
        distance = point.distance(point_on_line)

        if distance < min_distance:
            min_distance = distance
            nearest_line = line
            nearest_point_on_line = point_on_line

    return nearest_point_on_line

# Apply the function to each point in the GeoDataFrame
snapped_points = points_gdf.geometry.apply(lambda point: snap_to_line(point, lines_gdf))
points_list = snapped_points.tolist()

# Create a new GeoDataFrame for snapped points
snapped_points_gdf = gpd.GeoDataFrame(geometry=snapped_points, crs=points_gdf.crs)

# Optionally, save the snapped points to a new GeoJSON file
snapped_points_gdf.to_file('output/snapped_points.geojson', driver='GeoJSON')
count = 0
def split_line_at_points(line, points, tolerance=1e-10):
    snapped_points = [snap(point, line, tolerance) for point in points if line.intersects(point)]
    if snapped_points:
        multipoint = MultiPoint(snapped_points)
        # Split the line at the points, yielding a GeometryCollection
        collection = split(line, multipoint)
        print(f"Splitting line at points {multipoint}")
        # Filter the collection to include only LineString and MultiLineString objects
        segments = [geom for geom in collection.geoms if isinstance(geom, (LineString, MultiLineString))]
        return segments
    else:
        return [line]  # Return the original line if no points intersect


# Split each polyline using the snapped points
split_lines = []
# Iterate through each line in lines_gdf
for line in lines_gdf.geometry:
    # Call the split_line_at_points function for each line
    segments = split_line_at_points(line, snapped_points_gdf.geometry)
    split_lines.extend(segments)  # Add the resulting segments to the split_lines list


# Create a new GeoDataFrame for the split lines
split_lines_gdf = gpd.GeoDataFrame(geometry=split_lines, crs=lines_gdf.crs)

# Save the split lines to a new GeoJSON file
split_lines_gdf.to_file('output/split_lines.geojson', driver='GeoJSON')