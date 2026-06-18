"""
Base para algoritmos GEE que descargan UNA imagen raster, calculan
estadísticas opcionales y la cargan estilizada en QGIS.

Colapsa el patrón duplicado (~150 líneas) presente en los algoritmos de
Altura de Dosel / Biomasa raster:

    build_aoi -> safe_simplify -> to_ee_geometry -> buffer
      -> build_export_image (asset, máscaras, clasificación)   [HOOK]
      -> buffer-mask + clip                                    [base]
      -> compute_statistics (Excel)                            [HOOK opc.]
      -> prepare_for_export (p.ej. toByte)                     [HOOK opc.]
      -> export a Drive/GCS (export_router)  ó  descarga local
      -> QgsRasterLayer + apply_symbology + addMapLayer        [HOOK]
      -> fallback automático a Drive si el área es muy grande  [base]

La subclase implementa SOLO lo que varía. Ejemplo mínimo al final del
módulo. Reemplaza ~280 líneas por archivo por ~60-80.

NO importa `ee`, `requests` ni `pandas` a nivel de módulo: todo import
pesado es diferido a `processAlgorithm` (mejora el arranque del provider).
"""

import os

from qgis.core import (
    QgsProcessingException,
    QgsProject,
    QgsRasterLayer,
)

from ..algorithm_base import GeoForestGEEAlgorithmBase
from ...utils.aoi_builder import safe_simplify, to_ee_geometry
from ...utils.export_router import export_image, ExportMethod


class RasterJob:
    """Contenedor de lo que `build_export_image` entrega al orquestador.

    image        : ee.Image YA enmascarada por valor (pero SIN máscara de
                   buffer ni clip — eso lo añade la base de forma uniforme).
    out_filename : nombre base del .tif / tarea / Excel.
    label_prop   : nombre de banda (para stats `<label>_min`, etc.).
    scale        : escala en metros para export, descarga y estadísticas.
    drive_folder : carpeta destino en Drive/GCS para esta herramienta.
    """

    __slots__ = ('image', 'out_filename', 'label_prop', 'scale', 'drive_folder')

    def __init__(self, image, out_filename, label_prop, scale, drive_folder):
        self.image = image
        self.out_filename = out_filename
        self.label_prop = label_prop
        self.scale = scale
        self.drive_folder = drive_folder


class GeeRasterAlgorithm(GeoForestGEEAlgorithmBase):
    """Base para descarga de una imagen raster GEE estilizada."""

    # --- Parámetros de comportamiento (sobrescribibles por subclase) --------
    BUFFER_M = 250          # buffer interno de seguridad (metros)
    BUFFER_SEG = 30         # densificación del buffer (30=suave, 10=más vértices)
    REQUIRE_OUT_FOLDER = True
    STATS_ENABLED = True
    OUT_FOLDER = 'OUT_FOLDER'   # nombre canónico del parámetro de salida

    # =======================================================================
    # HOOKS que la subclase implementa
    # =======================================================================

    def build_export_image(self, ee, parameters, context, geom_ee_buffer, feedback):
        """OBLIGATORIO. Devuelve un RasterJob con la imagen ya enmascarada
        por valor (sin máscara de buffer ni clip)."""
        raise NotImplementedError

    def apply_symbology(self, rlayer, job):
        """OBLIGATORIO. Aplica el renderer/rampa de color a la capa."""
        raise NotImplementedError

    def compute_statistics(self, ee, image, region, job, out_dir, feedback):
        """OPCIONAL. Default: min/max/mean/median/stdDev -> Excel.
        Meta lo sobrescribe con área por clase. Errores no son fatales."""
        import pandas as pd
        try:
            reducer = (
                ee.Reducer.min()
                .combine(reducer2=ee.Reducer.max(), sharedInputs=True)
                .combine(reducer2=ee.Reducer.mean(), sharedInputs=True)
                .combine(reducer2=ee.Reducer.median(), sharedInputs=True)
                .combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
            )
            stats = image.reduceRegion(
                reducer=reducer, geometry=region,
                scale=job.scale, maxPixels=int(1e13),
            ).getInfo()
            lp = job.label_prop
            df = pd.DataFrame({
                "Estadística": ["Mínima", "Máxima", "Promedio", "Mediana",
                                "Desviación Estándar"],
                "Valor": [
                    stats.get(f'{lp}_min', 0), stats.get(f'{lp}_max', 0),
                    stats.get(f'{lp}_mean', 0), stats.get(f'{lp}_median', 0),
                    stats.get(f'{lp}_stdDev', 0),
                ],
            })
            excel_path = os.path.join(out_dir, f"{job.out_filename}_Estadisticas.xlsx")
            df.to_excel(excel_path, index=False)
            feedback.pushInfo(f"Archivo Excel generado: {excel_path}")
        except Exception as stat_err:
            feedback.reportError(
                f"Error al calcular estadísticas en GEE: {stat_err}",
                fatalError=False,
            )

    def prepare_for_export(self, image):
        """OPCIONAL. Transforma la imagen justo antes de exportar/descargar
        (p.ej. Meta hace .toByte()). Default: identidad."""
        return image

    # =======================================================================
    # Orquestación (común a todos)
    # =======================================================================

    def processAlgorithm(self, parameters, context, feedback):
        ee = self.ee  # import diferido vía propiedad del padre

        aoi = self.build_aoi_from_params(parameters, context, feedback)
        geom_simp = safe_simplify(aoi.geom_4326, 0.0001)

        export = self.read_export_params(parameters, context)
        crs = self.read_crs(parameters, context, fallback='EPSG:4326')
        out_dir = export['out_folder'] or ""

        if self.REQUIRE_OUT_FOLDER and not out_dir:
            raise QgsProcessingException(
                "Debe seleccionar una Carpeta Local de Destino."
            )

        self.init_gee(feedback)
        if feedback.isCanceled():
            return {}

        geom_ee = to_ee_geometry(geom_simp, ee).buffer(self.BUFFER_M, self.BUFFER_SEG)
        feedback.setProgress(10)

        job = self.build_export_image(ee, parameters, context, geom_ee, feedback)

        buffer_mask = ee.Image().byte().paint(geom_ee, 1)
        img_masked = job.image.updateMask(buffer_mask).clip(geom_ee.bounds())
        feedback.setProgress(30)

        if self.STATS_ENABLED:
            feedback.pushInfo("Calculando estadísticas en GEE...")
            self.compute_statistics(
                ee, img_masked, geom_ee.bounds(), job, out_dir, feedback
            )

        img_export = self.prepare_for_export(img_masked)
        feedback.setProgress(50)

        # --- Export a Drive / GCS -----------------------------------------
        if export['method'] != ExportMethod.LOCAL:
            export_image(
                method=export['method'], image=img_export,
                description=job.out_filename, region=geom_ee.bounds(),
                scale=job.scale, crs=crs, drive_folder=job.drive_folder,
                gcs_bucket=export['gcs_bucket'], feedback=feedback,
            )
            return {self.OUT_FOLDER: out_dir}

        # --- Descarga local directa ---------------------------------------
        return self._download_and_load(
            img_export, geom_ee, crs, job, out_dir, feedback
        )

    def _download_and_load(self, image, geom_ee, crs, job, out_dir, feedback):
        import requests
        feedback.pushInfo("Descargando Raster desde GEE...")
        try:
            url = image.getDownloadURL({
                'name': job.out_filename, 'crs': crs, 'scale': job.scale,
                'region': geom_ee.bounds(), 'format': 'GEO_TIFF',
            })
            r = requests.get(url, stream=True, timeout=300)
            r.raise_for_status()

            tif_file = os.path.join(out_dir, f"{job.out_filename}.tif")
            with open(tif_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if feedback.isCanceled():
                        return {}
                    if chunk:
                        f.write(chunk)
            if not os.path.exists(tif_file):
                raise QgsProcessingException("Error al guardar el archivo TIFF.")
        except QgsProcessingException:
            raise
        except Exception:
            feedback.pushInfo(
                "Área demasiado grande para descarga directa. Exportando a Drive..."
            )
            export_image(
                method=ExportMethod.DRIVE, image=image,
                description=job.out_filename, region=geom_ee.bounds(),
                scale=job.scale, crs=crs, drive_folder=job.drive_folder,
                feedback=feedback,
            )
            feedback.reportError(
                f"NOTA: Área inmensa. El raster '{job.out_filename}' fue "
                f"enviado a Google Drive.", fatalError=False,
            )
            return {}

        feedback.pushInfo("Cargando Raster a QGIS y aplicando simbología...")
        rlayer = QgsRasterLayer(tif_file, job.out_filename)
        if rlayer.isValid():
            self.apply_symbology(rlayer, job)
            rlayer.triggerRepaint()
            QgsProject.instance().addMapLayer(rlayer)

        feedback.setProgress(100)
        return {self.OUT_FOLDER: out_dir}
