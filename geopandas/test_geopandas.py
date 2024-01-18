import geopandas as gpd, fiona
import pandas as pd 

# fiona.drvsupport.supported_drivers['libkml'] = 'rw' 
fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
fiona.drvsupport.supported_drivers['KML'] = 'rw'

kml_file = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"

# For KML to GeoJSON
gdf = gpd.read_file(kml_file, driver='KML')

# Iterate through each layer in the GeoDataFrame
for layer_name, layer_data in gdf.groupby('folder'):
    # 'layer' is the column containing the layer information in the KML file
    # 'layer_name' is the name of the current layer
    # 'layer_data' is the GeoDataFrame containing only the current layer's data
    
    # Convert each layer to GeoJSON
    output_filename = f'output_{layer_name}.geojson'
    layer_data.to_file(output_filename, driver='GeoJSON')
