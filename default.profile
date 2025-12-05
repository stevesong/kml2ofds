[DEFAULT]
network_name = Default Network Name
network_id = 
kml_file_name = insertKMLfilename.kml
output_name_prefix = ISO-OP
physicalInfrastructureProvider_name = 
physicalInfrastructureProvider_id = 
networkProviders_name = 
networkProviders_id = 
network_links = https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json
# Separate ignored placemarks with a semi-colon
ignore_placemarks = 

# Threshold in meters for consolidating auto-generated nodes (default: 5000)
threshold_meters = 4000

# create a new span name based on the start and end node names
rename_spans_from_nodes = false
# write debug files to the output/debug/ directory
debug_enabled = false  
debug_output_directory = output/debug/

# In some cases a contiguious span may be multiple spans in the KML file. This option will merge them into a single span.
# The precision is the number of decimal places to consider when merging.
merge_contiguous_spans = false
merge_contiguous_spans_precision = 6

[DIRECTORY]
input_directory = input/
output_directory = output/