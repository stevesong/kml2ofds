from osgeo import gdal, ogr

srcDS = gdal.OpenEx('input.kml')
ds = gdal.VectorTranslate('output.json', srcDS, format='GeoJSON')