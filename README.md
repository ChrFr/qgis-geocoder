# BKG Geocoder

QGIS plugin for geocoding addresses with the geocoding service of the Bundesamt für Kartographie und Geodäsie (https://gdz.bkg.bund.de/).

The plugin takes a vector layer with address fields as an input to generate an output point layer with locations resulting from the geocoding. PostGIS-layers can also be updated inplace with the geocoding results.

The plugin also features a result inspector to switch between different results and a reverse geocoder to give alternatives to the found locations.