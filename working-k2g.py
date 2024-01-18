import geopandas as gpd, fiona
import pandas as pd 

# fiona.drvsupport.supported_drivers['libkml'] = 'rw' 
fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
# gpd.io.file.fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
# fiona.drvsupport.supported_drivers['kml'] = 'rw' # enable KML support which is disabled by default
# fiona.drvsupport.supported_drivers['KML'] = 'rw' # enable KML support which is disabled by default


kml_file = "/home/steve/Documents/OpenTelecomData/kml2ofds/data/MTN-Ghana-FOB-export.kml"

# Convert KML to GeoJSON
# gdf = gpd.read_file(kml_file)
# gdf.to_file('output.geojson', driver='GeoJSON')

gdf_list = []
for layer in fiona.listlayers(kml_file):    
    gdf = gpd.read_file(kml_file, driver='LIBKML', layer=layer)
    print(gdf)
    gdf_list.append(gdf)

gdf = gpd.GeoDataFrame(pd.concat(gdf_list, ignore_index=True))
