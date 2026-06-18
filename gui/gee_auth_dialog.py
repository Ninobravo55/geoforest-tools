import traceback
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QProgressBar, QFrame, QGroupBox
)
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.core import QgsTask, QgsApplication, QgsSettings, QgsMessageLog, Qgis
from ..utils.qt_compat import WINDOW_CONTEXT_HELP_HINT


class GEEAuthTask(QgsTask):
    """
    Tarea en segundo plano para manejar la autenticación de GEE
    sin congelar la interfaz de QGIS.
    """

    def __init__(self, project_id,
                 description="Autenticando Google Earth Engine"):
        super().__init__(description, QgsTask.CanCancel)
        self.project_id = project_id
        self.exception_msg = None
        self.success = False

    def run(self):
        try:
            import ee
            # Auth_mode por defecto lanza el navegador web automáticamente
            ee.Authenticate()

            # Si se proporcionó un proyecto, intentamos inicializar para
            # comprobar
            kwargs = {}
            if self.project_id:
                kwargs['project'] = self.project_id

            ee.Initialize(**kwargs)
            self.success = True
            return True

        except Exception as e:
            self.exception_msg = str(e)
            QgsMessageLog.logMessage(
                f"Error GEE Auth: {traceback.format_exc()}",
                "GeoForestTools",
                Qgis.Critical)
            return False

    def finished(self, result):
        if result and self.success:
            QgsMessageLog.logMessage(
                "GEE Autenticación Exitosa",
                "GeoForestTools",
                Qgis.Success)
            # Guardamos el proyecto en QgsSettings de forma global
            settings = QgsSettings()
            if self.project_id:
                settings.setValue('geoforesttools/gee_project', self.project_id)
            else:
                settings.remove('geoforesttools/gee_project')
        else:
            QgsMessageLog.logMessage(
                f"Fallo GEE Auth: {self.exception_msg}",
                "GeoForestTools",
                Qgis.Critical)


class GEEAuthDialog(QDialog):
    """
    Diálogo para ingresar el ID de proyecto y lanzar la tarea de autenticación OAuth.
    Muestra el estado actual de la autenticación GEE.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Configuración y Autenticación GEE"))
        self.resize(500, 320)
        self.setWindowFlags(
            self.windowFlags() & ~WINDOW_CONTEXT_HELP_HINT)

        layout = QVBoxLayout()
        self.setLayout(layout)

        # ── Banner de estado ──────────────────────────────────────────
        self.status_frame = QFrame()
        self.status_frame.setFrameShape(QFrame.StyledPanel)
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.status_icon = QLabel()
        self.status_icon.setFixedWidth(30)
        self.status_icon.setAlignment(Qt.AlignCenter)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_icon)
        status_layout.addWidget(self.status_label, 1)
        layout.addWidget(self.status_frame)

        # ── Grupo de configuración ────────────────────────────────────
        config_group = QGroupBox(self.tr("Configuración de Cuenta"))
        config_layout = QVBoxLayout(config_group)

        settings = QgsSettings()
        current_email = settings.value('geoforesttools/gee_email', '', type=str)
        current_project = settings.value(
            'geoforesttools/gee_project', '', type=str)

        # Campo Correo
        h_layout_email = QHBoxLayout()
        lbl_email = QLabel(self.tr("Correo GMAIL:"))
        lbl_email.setFixedWidth(130)
        self.txt_email = QLineEdit()
        if current_email:
            self.txt_email.setText(current_email)
        self.txt_email.setPlaceholderText(self.tr("Ej: usuario@gmail.com"))
        h_layout_email.addWidget(lbl_email)
        h_layout_email.addWidget(self.txt_email)
        config_layout.addLayout(h_layout_email)

        # Campo ID Proyecto
        h_layout_proj = QHBoxLayout()
        lbl_project = QLabel(self.tr("ID del proyecto GEE:"))
        lbl_project.setFixedWidth(130)
        self.txt_project = QLineEdit()
        if current_project:
            self.txt_project.setText(current_project)
        self.txt_project.setPlaceholderText(self.tr("Ej: mi-proyecto-12345"))
        h_layout_proj.addWidget(lbl_project)
        h_layout_proj.addWidget(self.txt_project)
        config_layout.addLayout(h_layout_proj)

        layout.addWidget(config_group)

        # Barra de progreso indeterminada
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # Indeterminado
        self.progress.hide()
        layout.addWidget(self.progress)

        # Botones
        btn_layout = QVBoxLayout()
        self.btn_auth = QPushButton(
            self.tr("Autenticar con Google y verificar conexión"))
        self.btn_save = QPushButton(self.tr("Guardar configuración"))
        self.btn_cancel = QPushButton(self.tr("Cerrar"))

        btn_layout.addWidget(self.btn_auth)

        h_btn = QHBoxLayout()
        h_btn.addStretch()
        h_btn.addWidget(self.btn_cancel)
        h_btn.addWidget(self.btn_save)
        btn_layout.addLayout(h_btn)

        layout.addLayout(btn_layout)

        self.btn_auth.clicked.connect(self.start_auth)
        self.btn_save.clicked.connect(self.save_config)
        self.btn_cancel.clicked.connect(self.reject)

        # ── Verificar estado al abrir ─────────────────────────────────
        self._update_status()

    def tr(self, message):
        return QCoreApplication.translate('GeoForestTools', message)

    def _check_gee_status(self):
        """Verifica si GEE está autenticado e inicializado."""
        try:
            import ee
            if getattr(ee.data, '_credentials', None) is not None:
                return True
            # Check if credentials exist on disk (user authenticated previously)
            import os
            try:
                cred_path = ee.oauth.get_credentials_path()
                if os.path.exists(cred_path):
                    return True
            except Exception:
                pass
        except ImportError:
            pass
        return False

    def _update_status(self):
        """Actualiza el banner de estado según la autenticación actual."""
        settings = QgsSettings()
        current_project = settings.value('geoforesttools/gee_project', '', type=str)
        current_email = settings.value('geoforesttools/gee_email', '', type=str)

        if self._check_gee_status():
            # ── Autenticado ───────────────────────────────────────────
            self.status_frame.setStyleSheet(
                "QFrame { background-color: #E8F5E9; border: 1px solid #4CAF50; border-radius: 6px; }")
            self.status_icon.setText("✅")
            self.status_icon.setStyleSheet("font-size: 20px;")

            detail_parts = []
            if current_project:
                detail_parts.append(f"Proyecto: <b>{current_project}</b>")
            if current_email:
                detail_parts.append(f"Cuenta: <b>{current_email}</b>")
            detail = "<br>".join(detail_parts) if detail_parts else ""

            self.status_label.setText(
                "<span style='color:#2E7D32; font-size:13px;'>"
                "<b>Google Earth Engine está autenticado y conectado.</b></span>"
                f"<br>{detail}"
                "<br><span style='color:#555; font-size:11px;'>"
                "Si deseas cambiar el proyecto o la cuenta, modifica los campos y haz clic en "
                "<i>Guardar configuración</i> o <i>Autenticar</i>.</span>"
            )
            self.btn_auth.setText(self.tr("Re-autenticar con otra cuenta de Google"))
        else:
            # ── No autenticado ────────────────────────────────────────
            self.status_frame.setStyleSheet(
                "QFrame { background-color: #FFF3E0; border: 1px solid #FF9800; border-radius: 6px; }")
            self.status_icon.setText("⚠️")
            self.status_icon.setStyleSheet("font-size: 20px;")
            self.status_label.setText(
                "<span style='color:#E65100; font-size:13px;'>"
                "<b>Google Earth Engine no está autenticado.</b></span>"
                "<br><span style='color:#555; font-size:11px;'>"
                "Ingresa tu correo y el ID de Proyecto GEE, luego haz clic en "
                "<i>Autenticar con Google</i> para conectar.</span>"
            )
            self.btn_auth.setText(self.tr("Autenticar con Google y verificar conexión"))

    def save_config(self):
        settings = QgsSettings()
        email = self.txt_email.text().strip()
        project = self.txt_project.text().strip()

        if email:
            settings.setValue('geoforesttools/gee_email', email)
        else:
            settings.remove('geoforesttools/gee_email')

        if project:
            settings.setValue('geoforesttools/gee_project', project)
        else:
            settings.remove('geoforesttools/gee_project')

        # Re-inicializar silenciosamente con la nueva configuración
        from ..utils.gee_init import background_gee_initialize
        background_gee_initialize(force=True)

        QMessageBox.information(
            self,
            self.tr("Guardado"),
            self.tr("Configuración guardada exitosamente."))

        # Actualizar estado visual
        self._update_status()

    def start_auth(self):
        try:
            pass
        except ImportError:
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr(
                    "No se encontró el módulo 'earthengine-api'. Instálalo primero.")
            )
            return

        project_id = self.txt_project.text().strip()

        if not project_id:
            QMessageBox.warning(
                self,
                self.tr("Atención"),
                self.tr("Debes ingresar un ID de Proyecto GEE (Google Cloud Project) válido para continuar. Si no tienes uno, créalo en Google Cloud Console.")
            )
            return

        # Configurar la tarea
        self.task = GEEAuthTask(project_id, self.tr(
            "Autenticando Google Earth Engine"))
        self.task.taskCompleted.connect(self.on_task_completed)
        self.task.taskTerminated.connect(self.on_task_failed)

        # Bloquear UI y mostrar progreso
        self.btn_auth.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.txt_project.setEnabled(False)
        self.txt_email.setEnabled(False)
        self.progress.show()

        # Iniciar tarea
        QgsApplication.taskManager().addTask(self.task)

    def on_task_completed(self):
        self.btn_auth.setEnabled(True)
        self.txt_project.setEnabled(True)
        self.txt_email.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.progress.hide()

        # Guardar automáticamente la configuración si la autenticación fue
        # exitosa
        self.save_config()

        # Actualizar banner al estado autenticado
        self._update_status()

        QMessageBox.information(
            self,
            self.tr("Éxito"),
            self.tr("Autenticación con Google Earth Engine completada correctamente.")
        )

    def on_task_failed(self):
        self.btn_auth.setEnabled(True)
        self.txt_project.setEnabled(True)
        self.txt_email.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.progress.hide()

        # Actualizar banner (sigue sin autenticar)
        self._update_status()

        msg = self.task.exception_msg if self.task and hasattr(
            self.task, 'exception_msg') else "Error desconocido"
        QMessageBox.critical(
            self,
            self.tr("Fallo en Autenticación"),
            self.tr(f"La autenticación falló:\n\n{msg}")
        )
