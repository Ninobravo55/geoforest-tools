import os
import matplotlib
matplotlib.use("Agg")  # backend headless, thread-safe en worker de Processing
import matplotlib.pyplot as plt
import pandas as pd

class GfwCharts:
    def __init__(self, out_dir, producto_nombre):
        self.out_dir = out_dir
        self.producto_nombre = producto_nombre
        # Limpiar el nombre para el archivo
        self.safe_name = self.producto_nombre.replace(" ", "_").replace("-", "_").replace(":", "")

    def generate_bosque_charts(self, gdf, shp_file):
        if gdf.empty:
            return
            
        if 'Cobertura' in gdf.columns:
            import re
            
            # Calcular área en Hectáreas si no está
            if 'Area_Ha' not in gdf.columns:
                if gdf.crs and gdf.crs.is_geographic:
                    gdf_proj = gdf.to_crs(epsg=32718) # Aproximación general para área
                    gdf['Area_Ha'] = gdf_proj.geometry.area / 10000
                else:
                    gdf['Area_Ha'] = gdf.geometry.area / 10000

            # Agrupar por Cobertura (strings como "Bosque al 2025", "Pérdida bosque 2001")
            areas_por_cobertura = gdf.groupby('Cobertura')['Area_Ha'].sum()
            
            anios_perdida = []
            perdida_dict = {}
            for cob, area in areas_por_cobertura.items():
                if "Pérdida" in str(cob):
                    match = re.search(r'\d{4}', str(cob))
                    if match:
                        year = int(match.group())
                        anios_perdida.append(year)
                        perdida_dict[year] = area
                        
            max_anio = max(anios_perdida) if anios_perdida else 2025
            total_bosque_2000 = areas_por_cobertura.sum()
            total_perdida = sum(perdida_dict.values())
            
            anios = [2000]
            bosque_por_anio = [total_bosque_2000]
            perdida_acumulada_por_anio = [0]
            perdida_anual = [0]
            
            bosque_actual = total_bosque_2000
            perdida_acumulada = 0
            for anio in range(2001, max_anio + 1):
                perdida = perdida_dict.get(anio, 0)
                bosque_actual -= perdida
                perdida_acumulada += perdida
                
                anios.append(anio)
                bosque_por_anio.append(bosque_actual)
                perdida_acumulada_por_anio.append(perdida_acumulada)
                perdida_anual.append(perdida)
            
            plt.figure(figsize=(20, 10))
            ax1 = plt.gca()
            
            # Gráfico de barras principal: Bosque remanente
            bars = ax1.bar([str(a) for a in anios], bosque_por_anio, color='#2ca02c', edgecolor='black', width=0.6, label='Bosque Remanente')
            
            plt.title('Dinámica de Bosque y Pérdida (2000 - 2025)', fontsize=20, pad=20, fontweight='bold')
            ax1.set_xlabel('Año', fontsize=14, fontweight='bold')
            ax1.set_ylabel('Área de Bosque (Hectáreas)', fontsize=14, fontweight='bold')
            ax1.tick_params(axis='x', rotation=45, labelsize=12)
            ax1.tick_params(axis='y', labelsize=12)
            ax1.grid(axis='y', linestyle='--', alpha=0.7)
            
            # Ampliar el límite Y para que la leyenda y el texto no tapen las barras
            ax1.set_ylim(0, total_bosque_2000 * 1.25)
            
            # Textos en las barras de bosque
            for bar in bars:
                height = bar.get_height()
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    height + (total_bosque_2000 * 0.01),
                    f"{height:,.0f}",
                    ha='center',
                    va='bottom',
                    rotation=90,
                    fontsize=11,
                    fontweight='bold',
                    color='darkgreen'
                )

            # Agregar texto resumen de pérdida en el gráfico
            resumen_texto = (
                f"RESUMEN (2001-{max_anio}):\n"
                f"Bosque Inicial (2000): {total_bosque_2000:,.0f} ha\n"
                f"Bosque Final ({max_anio}): {bosque_actual:,.0f} ha\n"
                f"Pérdida Total Acumulada: {total_perdida:,.0f} ha"
            )
            ax1.text(
                0.98, 0.95, resumen_texto, 
                transform=ax1.transAxes,
                fontsize=14,
                verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.9, edgecolor='gray')
            )

            # Graficar la pérdida anual como línea en un eje secundario
            ax2 = ax1.twinx()
            ax2.plot([str(a) for a in anios], perdida_anual, color='red', marker='o', linewidth=3, markersize=8, label='Pérdida Anual')
            ax2.set_ylabel('Pérdida Anual (Hectáreas)', fontsize=14, color='red', fontweight='bold')
            ax2.tick_params(axis='y', labelcolor='red', labelsize=12)
            
            max_perdida = max(perdida_anual) if perdida_anual else 1
            ax2.set_ylim(0, max_perdida * 3) 
            
            # Combinar leyendas
            lines_1, labels_1 = ax1.get_legend_handles_labels()
            lines_2, labels_2 = ax2.get_legend_handles_labels()
            
            # Mover la leyenda afuera o arriba
            ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=2, fontsize=12)

            chart_path = os.path.join(self.out_dir, f"{self.safe_name}_Grafico_Bosque_y_Perdida.png")
            plt.tight_layout()
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()

            df_stats = pd.DataFrame({
                "Año": anios,
                "Bosque_Remanente_Ha": bosque_por_anio,
                "Perdida_Anual_Ha": perdida_anual,
                "Perdida_Acumulada_Ha": perdida_acumulada_por_anio
            })
            excel_path = os.path.join(self.out_dir, f"{self.safe_name}_Estadisticas.xlsx")
            try:
                df_stats.to_excel(excel_path, index=False)
            except Exception:
                csv_path = excel_path.replace('.xlsx', '.csv')
                df_stats.to_csv(csv_path, index=False, encoding='utf-8')

    def generate_perdida_charts(self, gdf, shp_file):
        if gdf.empty or 'Anual' not in gdf.columns:
            return

        # Calcular área
        if gdf.crs and gdf.crs.is_geographic:
            gdf_proj = gdf.to_crs(epsg=32718)
            gdf['area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
        else:
            gdf['area_ha'] = (gdf.geometry.area / 10000).round(4)

        # GFW lossyear es el año - 2000
        gdf['Anio'] = gdf['Anual'] + 2000
        
        # Clasificar área
        def clasificar(area):
            if area < 1: return '<1 ha'
            elif area < 5: return '1–5 ha'
            elif area < 50: return '5–50 ha'
            elif area < 500: return '50–500 ha'
            else: return '>500 ha'
            
        gdf['Clase'] = gdf['area_ha'].apply(clasificar)
        
        # Guardar cambios en el SHP
        if shp_file:
            gdf.to_file(shp_file, encoding='utf-8')
            
        # 1. Gráfico Anual
        perdida_agrupado = gdf.groupby('Anio')['area_ha'].sum().reset_index()
        perdida_agrupado = perdida_agrupado.sort_values('Anio')

        plt.figure(figsize=(14, 7))
        bars = plt.bar(perdida_agrupado['Anio'].astype(int).astype(str), perdida_agrupado['area_ha'], color='red', edgecolor='black', width=0.6)
        plt.title('Pérdida de Bosque Anual (GFW)', fontsize=14, pad=20)
        plt.xlabel('Año', fontsize=12)
        plt.ylabel('Área de Pérdida (Hectáreas)', fontsize=12)
        plt.xticks(rotation=45)
        plt.grid(axis='y', linestyle='--', alpha=0.7)

        for bar in bars:
            height = bar.get_height()
            if height > 0:
                plt.text(
                    bar.get_x() + bar.get_width() / 2,
                    height / 2,  # Centro vertical de la barra
                    f"{height:,.0f}",
                    ha='center',
                    va='center',
                    rotation=90,
                    color='white',
                    fontweight='bold',
                    fontsize=9
                )

        chart_path = os.path.join(self.out_dir, f"{self.safe_name}_Grafico_Anual.png")
        plt.tight_layout()
        plt.savefig(chart_path, dpi=300, bbox_inches='tight')
        plt.close()

        orden_clases = ['<1 ha', '1–5 ha', '5–50 ha', '50–500 ha', '>500 ha']
        # 3. Gráfico Apilado: Pérdida Anual de Bosque por Tamaño de Clase
        agrupar_clase = gdf.groupby(['Anio', 'Clase'], observed=False)['area_ha'].sum().unstack(fill_value=0)
        
        # Asegurar el orden de las columnas
        columnas_existentes = [c for c in orden_clases if c in agrupar_clase.columns]
        agrupar_clase = agrupar_clase[columnas_existentes]
        
        colores_dict = {
            '<1 ha': '#f6942e',
            '1–5 ha': '#f6d32e',
            '5–50 ha': '#61be53',
            '50–500 ha': '#7c2d0e',
            '>500 ha': '#ec2dc9'
        }
        
        if not agrupar_clase.empty:
            ax = agrupar_clase.plot(
                kind='bar', stacked=True, figsize=(15, 8),
                color=[colores_dict[c] for c in agrupar_clase.columns],
                edgecolor='black', width=0.75
            )
            
            # Ajustar ymax para dar mucho más espacio al texto sobre las barras
            sum_por_anio = agrupar_clase.sum(axis=1)
            ymax2 = sum_por_anio.max() * 1.35 if not sum_por_anio.empty else 100
            plt.ylim(0, ymax2)
            
            # Colocar la leyenda en la parte superior izquierda
            plt.legend(title='Tamaño de Clase', loc='upper left', fontsize=11, edgecolor='black', title_fontsize=12)
            
            plt.title('Pérdida Anual de Bosque por Tamaño de Clase (GFW)', fontweight='bold', fontsize=16)
            plt.xlabel('Año', fontweight='bold', fontsize=12)
            plt.ylabel('Pérdida (hectáreas)', fontweight='bold', fontsize=12)
            plt.xticks(rotation=45)
            plt.grid(axis='y', linestyle='--', alpha=0.5)
            
            # Añadir textos del total sobre cada barra apilada
            for i, total in enumerate(sum_por_anio):
                if total > 0:
                    plt.text(i, total + (ymax2 * 0.01), f"{total:,.0f}",
                             ha='center', va='bottom', rotation=90, fontsize=10, fontweight='bold', color='black')
            
            plt.tight_layout()
            out_img2 = os.path.join(self.out_dir, f"{self.safe_name}_Grafico_Apilado_Clases.png")
            plt.savefig(out_img2, dpi=300, bbox_inches='tight')
            plt.close()

        # Excel de estadísticas generales
        excel_path = os.path.join(self.out_dir, f"{self.safe_name}_Estadisticas.xlsx")
        try:
            perdida_agrupado.to_excel(excel_path, index=False)
        except Exception:
            csv_path = excel_path.replace('.xlsx', '.csv')
            perdida_agrupado.to_csv(csv_path, index=False, encoding='utf-8')
        
        # Excel de tabla resumen por clase (con fila y columna de totales)
        agrupar_clase_export = agrupar_clase.copy()
        agrupar_clase_export['TOTAL'] = agrupar_clase_export.sum(axis=1)
        
        totales = agrupar_clase_export.sum(axis=0).to_frame().T
        totales.index = ['TOTAL']
        
        agrupar_clase_export = pd.concat([agrupar_clase_export, totales])
        agrupar_clase_export.index.name = 'Anio'
        
        excel_clase_path = os.path.join(self.out_dir, f"{self.safe_name}_Tabla_Resumen_Clase.xlsx")
        try:
            agrupar_clase_export.reset_index().to_excel(excel_clase_path, index=False)
        except Exception:
            csv_clase_path = excel_clase_path.replace('.xlsx', '.csv')
            agrupar_clase_export.reset_index().to_csv(csv_clase_path, index=False, encoding='utf-8')

    def generate_ganancia_charts(self, gdf, shp_file):
        if gdf.empty:
            return
            
        if 'Area_Ha' not in gdf.columns:
            gdf_proj = gdf.to_crs(epsg=32718)
            gdf['Area_Ha'] = gdf_proj.geometry.area / 10000

        total_ganancia = gdf['Area_Ha'].sum()
        
        plt.figure(figsize=(8, 6))
        plt.bar(['Ganancia 2001-2012'], [total_ganancia], color='blue')
        plt.title('Ganancia de Bosque (GFW)')
        plt.ylabel('Área (Hectáreas)')
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        
        for i, v in enumerate([total_ganancia]):
            plt.text(i, v + (v*0.01), f"{v:,.2f} Ha", ha='center')

        chart_path = os.path.join(self.out_dir, f"{self.safe_name}_Grafico_Ganancia.png")
        plt.savefig(chart_path, dpi=300, bbox_inches='tight')
        plt.close()

        excel_path = os.path.join(self.out_dir, f"{self.safe_name}_Estadisticas.xlsx")
        df = pd.DataFrame([{"Categoria": "Ganancia", "Area_Ha": total_ganancia}])
        try:
            df.to_excel(excel_path, index=False)
        except Exception:
            csv_path = excel_path.replace('.xlsx', '.csv')
            df.to_csv(csv_path, index=False, encoding='utf-8')
