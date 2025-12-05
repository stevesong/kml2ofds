[DEFAULT]
network_name = Bayobab Africa - MTN Ghana Full
network_id = 
kml_file_name = MTN-Ghana-full.kml
output_name_prefix = GHA-MTN-FULL
physicalInfrastructureProvider_name = Bayobab Africa
physicalInfrastructureProvider_id = BAY-1
networkProviders_name = MTN Ghana
networkProviders_id = MTN-1
network_links = https://raw.githubusercontent.com/Open-Telecoms-Data/open-fibre-data-standard/0__3__0/schema/network-schema.json
# Separate ignored placemarks with a semi-colon
ignore_placemarks = ^KK\d*;^TP\d*;^HH\d*;^HK\d*;.*HH\)$
# ignore_placemarks = 

# Threshold in meters for consolidating auto-generated nodes (default: 5000)
threshold_meters = 4000

rename_spans_from_nodes = true  # create a new span name based on the start and end node names
debug_enabled = false  # write debug files to the output/debug/ directory
debug_output_directory = output/debug/

[DIRECTORY]
input_directory = input/
output_directory = output/
