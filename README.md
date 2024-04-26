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

## Install kml2ofds

### Using pipx

The recommended way to install `kml2ofds` as a user is with [pipx][pipx].

First ensure [pipx is installed](https://pipx.pypa.io/stable/#install-pipx), then to install the latest `kml2ofds`:

```sh
pipx install git+https://github.com/stevesong/kml2ofds.git@main
```

and then the command should be available to use (you may need to restart your shell):

```sh
kml2ofds [--options]
```

### Using pip

To install `kml2ofds` inside an existing python virtual environment or conda environment:

```sh
# First activate your environment, then:
pip install git+https://github.com/stevesong/kml2ofds.git@main
```

## Developing kml2ofds

To test running `kml2ofds` while developing, it's necessary to install the package as editable using `pip -e`, e.g.:

```sh
cd path/to/kml2ofds/
source .venv/bin/activate # If using a python virtual environment

# Install as editable
pip install -e .
```

To add, remove or update requirements, edit the dependencies section in `pyproject.toml`, then run `pip install -e .` again to update the dependencies in your virtual environment.


[ofds-repo]: <https://github.com/Open-Telecoms-Data/open-fibre-data-standard>
[ofds-docs]: <https://open-fibre-data-standard.readthedocs.io/en/latest/reference/schema.html>
[pipx]: <https://github.com/pypa/pipx/>
