# KML2OFDS

KML2OFDS is a python script for converting KML maps of fibre optic network infrastructure to the [Open Fibre Data Standard][ofds-repo].  Consult the [documentation][ofds-docs] for more info.

## Assumptions

Core to OFDS is the principle that any span of fibre must be terminated at either end by a Point of Presence of some kind.  Here we are using Point of Presence in a very loose sense.  This might be a simple access point such as a manhole or it might be a full point of presence access point. Consult the standard for more details.

As such, KML2OFDS expects a KML file that contains both fibre optic routes as well as points of presence.

In broad strokes the script:

* parses a KML document for features and separates them into a collection of nodes (any point feature in the KML) and spans (any LineString or collection of LineStrings) in the KML;
* checks for duplicate nodes based on a combination of node 'name' and node latitude, longitude based on an adjustable level of location precision
* the script snaps nodes to the closest point on the closest span, if they are not already somewhere on a span;
* it then breaks each span at every point where a node intersects a span, resulting in a larger number of shorter spans;
* a node is then associated each with the "start" and "end" of each of the spans; and,
* adds meta data to the spans and nodes. at the moment on the most basic meta data is added during the export process

[ofds-repo]: <https://github.com/Open-Telecoms-Data/open-fibre-data-standard>
[ofds-docs]: <https://open-fibre-data-standard.readthedocs.io/en/latest/reference/schema.html>
