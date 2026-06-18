"""
Helpers de simbología — factory de renderers para QGIS.

Centraliza patrones repetidos en ~7 algoritmos del plugin:
  - Crear QgsCategorizedSymbolRenderer a partir de una lista de
    (valor, etiqueta, color).
  - Crear QgsSingleSymbolRenderer con un color sólido.
  - Aplicar simbología + guardar sidecar .qml en una sola llamada.

NO reemplaza la lógica de paletas específicas de cada producto (esos
mapas valor→color son específicos del dataset). Sólo elimina las 5-10
líneas repetidas para CONSTRUIR el renderer una vez que tenés la paleta.
"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsSymbol,
)
from qgis.PyQt.QtGui import QColor

from .style_utils import save_qml_sidecar


def make_categorized_renderer(layer, attribute, categories,
                              stroke_color='transparent'):
    """
    Construye un QgsCategorizedSymbolRenderer.

    Parámetros
    ----------
    layer : QgsVectorLayer
        Capa sobre la que se basará el tipo de geometría.
    attribute : str
        Nombre del atributo categórico (ej: 'NAME', 'TIPO').
    categories : iterable de tuplas
        Cada tupla es (valor, etiqueta, color). `color` puede ser:
          - string hex ('#ff0000'), nombre Qt ('red'), o
          - QColor directamente.
    stroke_color : str o QColor
        Color del borde. Por defecto 'transparent' para que no se vea
        el contorno (típico de mapas categóricos rasterizados como vector).
    """
    cats = []
    for value, label, color in categories:
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.setColor(_to_qcolor(color))
        if sym.symbolLayerCount() > 0:
            sym.symbolLayer(0).setStrokeColor(_to_qcolor(stroke_color))
        cats.append(QgsRendererCategory(value, sym, label))
    return QgsCategorizedSymbolRenderer(attribute, cats)


def make_single_symbol_renderer(layer, color, stroke_color='transparent'):
    """Construye un QgsSingleSymbolRenderer de un color sólido."""
    sym = QgsSymbol.defaultSymbol(layer.geometryType())
    sym.setColor(_to_qcolor(color))
    if sym.symbolLayerCount() > 0:
        sym.symbolLayer(0).setStrokeColor(_to_qcolor(stroke_color))
    return QgsSingleSymbolRenderer(sym)


def apply_and_save(layer, renderer, source_path):
    """
    Aplica el renderer al layer y guarda el sidecar .qml con extensión
    correcta (delegando en save_qml_sidecar — fix B-02 incluido).
    """
    layer.setRenderer(renderer)
    return save_qml_sidecar(layer, source_path)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _to_qcolor(c):
    """Acepta string ('#ff0000', 'red'), tupla (r,g,b[,a]), o QColor."""
    if isinstance(c, QColor):
        return c
    if isinstance(c, str):
        return QColor(c)
    if isinstance(c, (tuple, list)):
        return QColor(*c)
    raise TypeError(f"Color no reconocido: {c!r}")
