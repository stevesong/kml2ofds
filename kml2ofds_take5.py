import geopandas as gpd
from shapely.ops import nearest_points, split
from shapely.geometry import MultiPolygon
import uuid
# import matplotlib
# matplotlib.use('Qt5Agg')  # Choose an appropriate backend
# import matplotlib.pyplot as plt


# Load the GeoJSON files into GeoDataFrames
points_gdf = gpd.read_file("output/points.geojson")
lines_gdf = gpd.read_file("output/polylines.geojson")
num_lines = len(lines_gdf)
print("Number of lines:", num_lines)

# Ensure CRS match, if not, reproject
if points_gdf.crs != lines_gdf.crs:
    points_gdf = points_gdf.to_crs(lines_gdf.crs)

# Function to find the nearest line to each point and find the nearest point on that line to the point
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

# Apply the snap_to_line function to each point in the points GeoDataFrame
snapped_points = points_gdf.geometry.apply(lambda point: snap_to_line(point, lines_gdf))
total_points = snapped_points.size
print("Total number of points found:", total_points)

snapped_points_gdf = gpd.GeoDataFrame(points_gdf.drop(columns='geometry'), geometry=snapped_points, crs=points_gdf.crs)

# Save the snapped points to a new GeoJSON file
snapped_points_gdf.to_file('output/snapped_points.geojson', driver='GeoJSON')


# Iterate over the joined data and split linestrings
split_lines = []

# Iterate over the lines and find the snapped points that intersect each line
for idx, line_row in lines_gdf.iterrows():
    polyline_name = line_row['name']
    buffered_points = []
    point_names = []
    
    for point_idx, point_row in snapped_points_gdf.iterrows():
        point = point_row.geometry
        point_name = point_row['name']
        buffered_point = point.buffer(1e-5)
        buffered_points.append(buffered_point)
        
        if line_row.geometry.intersects(buffered_point):
            point_names.append(point_name)  # Capture the name of the intersecting point
    
    buffered_area = MultiPolygon(buffered_points)
    
    if line_row.geometry.intersects(buffered_area):
        split_line = split(line_row.geometry, buffered_area)
        for segment in split_line.geoms:
            # Create a unique identifier for each line segment
            segment_uuid = str(uuid.uuid4())
            # Include both polyline and point names with the geometry
            split_lines.append((segment_uuid, segment, polyline_name, ", ".join(point_names)))
    else:
        # Generate a UUID for the original line if no intersection
        segment_uuid = str(uuid.uuid4())
        split_lines.append((segment_uuid, line_row.geometry, polyline_name, ""))


print("Number of segments found:", len(split_lines))

# Create a new GeoDataFrame from the split linestrings
split_linestrings_gdf = gpd.GeoDataFrame(split_lines, columns=['uuid', 'geometry', 'polyline_name', 'point_names'], crs=lines_gdf.crs)

# Set the 'uuid' column as the index of the GeoDataFrame
split_linestrings_gdf.set_index('uuid', inplace=True)

# fig, ax = plt.subplots()
# split_linestrings_gdf.plot(ax=ax, color='blue')
# snapped_points_gdf.plot(ax=ax, color='red', markersize=5)
# plt.show()

# # Save the split lines to a new GeoJSON file
split_linestrings_gdf.to_file('output/split_lines.geojson', driver='GeoJSON')
# print(split_linestrings_gdf.head())