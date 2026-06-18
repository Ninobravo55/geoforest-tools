def classFactory(iface):
    from .plugin import GeoForestToolsPlugin
    return GeoForestToolsPlugin(iface)
