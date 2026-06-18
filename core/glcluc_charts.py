import os
import traceback

class GlclucCharts:
    def __init__(self, out_dir, producto_nombre):
        self.out_dir = out_dir
        self.producto_nombre = producto_nombre
        self.safe_name = producto_nombre.replace(" ", "_").replace(":", "")

    def generate_charts(self, gdf, shp_file, label_prop):
        # Generic wrapper para compatibilidad antigua
        if "Dinamica" in self.producto_nombre:
            self.generate_dinamica_charts(gdf, shp_file, label_prop)
        elif "Ganancia" in self.producto_nombre:
            self.generate_ganancia_charts(gdf, shp_file, label_prop)
        else:
            self.generate_altura_charts(gdf, shp_file, label_prop)

    def generate_dinamica_charts(self, gdf, shp_file=None, label_prop='Dinamica'):
        if gdf.empty: return
        
        try:
            import pandas as pd
            import matplotlib.pyplot as plt
            import numpy as np
            
            # 1. Definir Mapeo y Colores
            mapa_nombres = {
                1: 'Bosque estable',
                2: 'Perdida forestal',
                3: 'Ganancia forestal',
                4: 'Degradacion'
            }
            colores_dict = {
                'Bosque estable': '#006400',
                'Perdida forestal': '#FF0000',
                'Ganancia forestal': '#00FF00',
                'Degradacion': '#FF8C00'
            }
            
            # Asegurar TIPO
            if 'TIPO' not in gdf.columns:
                col_name = label_prop if label_prop in gdf.columns else 'label'
                gdf['TIPO'] = gdf[col_name].map(mapa_nombres)
            
            # Asegurar area_ha
            if 'area_ha' not in gdf.columns and 'Area_ha' in gdf.columns:
                gdf['area_ha'] = gdf['Area_ha']
            elif 'area_ha' not in gdf.columns:
                if gdf.crs and gdf.crs.is_geographic:
                    gdf_proj = gdf.to_crs("EPSG:32718")
                    gdf['area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
                else:
                    gdf['area_ha'] = (gdf.geometry.area / 10000).round(4)
                    
            if shp_file:
                driver = 'GPKG' if shp_file.lower().endswith('.gpkg') else 'ESRI Shapefile'
                gdf.to_file(shp_file, driver=driver, encoding='utf-8')
                
            resumen = gdf.groupby('TIPO')['area_ha'].sum().reset_index()
            # Ordenar según importancia o código original
            orden_tipo = ['Bosque estable', 'Perdida forestal', 'Ganancia forestal', 'Degradacion']
            resumen['TIPO'] = pd.Categorical(resumen['TIPO'], categories=orden_tipo, ordered=True)
            resumen = resumen.sort_values('TIPO')
            
            # 2. Exportar Excel
            total_area = resumen['area_ha'].sum()
            export_df = resumen.copy()
            fila_total = pd.DataFrame([{'TIPO': 'TOTAL', 'area_ha': total_area}])
            export_df = pd.concat([export_df, fila_total], ignore_index=True)
            export_df.rename(columns={'area_ha': 'Área Total (Hectáreas)'}, inplace=True)
            
            excel_path = os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Estadisticas.xlsx")
            try:
                export_df.to_excel(excel_path, index=False)
            except Exception:
                csv_path = excel_path.replace('.xlsx', '.csv')
                export_df.to_csv(csv_path, index=False, encoding='utf-8')
            
            # Preparar colores para gráficos
            colores_grafico = [colores_dict.get(n, '#000000') for n in resumen['TIPO']]
            
            # 3. Gráfico de Barras
            plt.figure(figsize=(10, 6))
            bars = plt.bar(resumen['TIPO'], resumen['area_ha'], color=colores_grafico, edgecolor='black')
            
            for bar in bars:
                yval = bar.get_height()
                if yval > 0:
                    plt.text(bar.get_x() + bar.get_width()/2, yval + (max(resumen['area_ha'])*0.01), f'{yval:,.1f} ha', ha='center', va='bottom', fontsize=11, fontweight='bold')
                    
            plt.title('Dinámica Forestal 2000 - 2020', fontsize=16, fontweight='bold', pad=15)
            plt.xlabel('Tipo de Dinámica', fontsize=12, fontweight='bold')
            plt.ylabel('Área (Hectáreas)', fontsize=12, fontweight='bold')
            plt.grid(axis='y', linestyle='--', alpha=0.4)
            plt.tight_layout()
            
            chart_bar = os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Barras.jpg")
            plt.savefig(chart_bar, dpi=300)
            plt.close()
            
            # 4. Gráfico Circular (Pie)
            plt.figure(figsize=(9, 8))
            pie_df = resumen[resumen['area_ha'] > 0].copy()
            colores_pie = [colores_dict.get(n, '#000000') for n in pie_df['TIPO']]
            
            wedges, texts, autotexts = plt.pie(
                pie_df['area_ha'],
                labels=pie_df['TIPO'],
                colors=colores_pie,
                autopct='%1.1f%%',
                startangle=140,
                pctdistance=0.8,
                wedgeprops={'edgecolor': 'black', 'linewidth': 1}
            )
            plt.setp(autotexts, size=11, weight="bold", color="white")
            # Ajustar color texto de Ganancia o Bosque si es muy claro
            for autotext, color_name in zip(autotexts, pie_df['TIPO']):
                if color_name in ['Ganancia forestal']: autotext.set_color('black')
                
            plt.title("Proporción de la Dinámica Forestal", fontweight='bold', fontsize=16)
            plt.tight_layout()
            
            chart_pie = os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Circular.jpg")
            plt.savefig(chart_pie, dpi=300)
            plt.close()
            
            # 5. Gráfico de "Tendencia" (Barras Horizontales Comparativas)
            plt.figure(figsize=(12, 5))
            y_pos = np.arange(len(resumen['TIPO']))
            bars_h = plt.barh(y_pos, resumen['area_ha'], color=colores_grafico, edgecolor='black', height=0.6)
            
            plt.yticks(y_pos, resumen['TIPO'], fontsize=12, fontweight='bold')
            plt.xlabel('Área (Hectáreas)', fontsize=12, fontweight='bold')
            plt.title('Tendencia / Distribución de Dinámica Forestal', fontsize=16, fontweight='bold')
            
            for bar in bars_h:
                width = bar.get_width()
                if width > 0:
                    plt.text(width + (max(resumen['area_ha'])*0.01), bar.get_y() + bar.get_height()/2, f'{width:,.1f} ha', va='center', fontsize=11, fontweight='bold')
                    
            plt.grid(axis='x', linestyle='--', alpha=0.4)
            plt.tight_layout()
            
            chart_trend = os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Tendencia.jpg")
            plt.savefig(chart_trend, dpi=300)
            plt.close()
            
        except Exception as e:
            print("Error generando gráficos de Dinámica GLCLUC:", str(e))
            traceback.print_exc()

    def generate_altura_charts(self, gdf, shp_file=None, label_prop='b1'):
        if gdf.empty: return
        try:
            import matplotlib.pyplot as plt
            
            if 'area_ha' not in gdf.columns:
                if gdf.crs and gdf.crs.is_geographic:
                    gdf_proj = gdf.to_crs("EPSG:32718")
                    gdf['area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
                else:
                    gdf['area_ha'] = (gdf.geometry.area / 10000).round(4)
            
            # 2. Guardar cambios
            if shp_file:
                driver = 'GPKG' if shp_file.lower().endswith('.gpkg') else 'ESRI Shapefile'
                gdf.to_file(shp_file, driver=driver, encoding='utf-8')
            
            col_name = label_prop if label_prop in gdf.columns else 'label'
            if col_name not in gdf.columns and 'b1' in gdf.columns: col_name = 'b1'
            elif col_name not in gdf.columns and 'Altura' in gdf.columns: col_name = 'Altura'
                
            resumen = gdf.groupby(col_name)['area_ha'].sum().reset_index()
            
            excel_path = os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Estadisticas.xlsx")
            try:
                resumen.to_excel(excel_path, index=False)
            except Exception:
                csv_path = excel_path.replace('.xlsx', '.csv')
                resumen.to_csv(csv_path, index=False, encoding='utf-8')
            
            plt.figure(figsize=(12, 6))
            bars = plt.bar(resumen[col_name].astype(str), resumen['area_ha'], color='#005a00')
            plt.title(f'Distribución de {self.producto_nombre}', fontsize=14, fontweight='bold')
            plt.xlabel('Altura (m)', fontsize=12)
            plt.ylabel('Área (ha)', fontsize=12)
            plt.tight_layout()
            plt.savefig(os.path.join(self.out_dir, f"GLCLUC_{self.safe_name}_Grafico.png"), dpi=300)
            plt.close()
        except: traceback.print_exc()

    def generate_ganancia_charts(self, gdf, shp_file=None, label_prop='b1'):
        # Similar simple fallback
        self.generate_altura_charts(gdf, shp_file, label_prop)
