"""
GeoForest Tools — paquete de utilidades compartidas.

Centraliza la lógica que antes estaba duplicada en cada algoritmo:
  - aoi_builder: construcción de AOI (capa o extent) en EPSG:4326 y
    conversión segura a ee.Geometry sin truncar GeometryCollection.
  - gee_init: wrapper único para inicializar Earth Engine.
  - style_utils: sidecar .qml con extensión correcta independiente del
    formato fuente (shp, gpkg, tif).

No depende de Earth Engine en import time — `ee` se pasa como parámetro
a las funciones que lo requieren, para evitar fallos de import si la
librería no está instalada.
"""
