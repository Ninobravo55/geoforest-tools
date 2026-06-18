import os

class GeobosquesCharts:
    def __init__(self, out_dir, producto):
        self.out_dir = out_dir
        self.producto = producto

    def generate_bosque_charts(self, gdf, shp_file=None):
        col_name = next((c for c in ['Cobertura', 'cobertura', 'DN', 'label'] if c in gdf.columns), None)
        if not col_name: raise KeyError("No se encontró columna válida (Cobertura, DN) en los datos.")
        
        # 1. Mapeo de Nombres
        mapa_nombres = {
            1: 'Agua',
            2: 'Bosque al 2024',
            3: 'Deforestacion del 2001 al 2024',
            4: 'No Bosque al 2000',
            5: 'Sin informacion'
        }
        if 'NAME' not in gdf.columns:
            gdf['NAME'] = gdf[col_name].map(mapa_nombres)
            
        # 2. Area en Hectáreas con 4 decimales
        if gdf.crs and gdf.crs.is_geographic:
            # Reproyectar a UTM local (EPSG:32718 es el default de la zona, aunque sería mejor dinámico si se proveyó)
            gdf_proj = gdf.to_crs("EPSG:32718")
            gdf['Area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
        else:
            gdf['Area_ha'] = (gdf.geometry.area / 10000).round(4)
            
        # 3. Guardar cambios en el SHP/GPKG
        if shp_file:
            import os
            driver = 'GPKG' if shp_file.lower().endswith('.gpkg') else 'ESRI Shapefile'
            if os.path.exists(shp_file):
                try: os.remove(shp_file)
                except: pass
            gdf.to_file(shp_file, driver=driver, encoding='utf-8')
        
        # 4. Agrupar para el gráfico
        area_df = gdf.groupby('NAME')['Area_ha'].sum().reset_index().sort_values(by='Area_ha', ascending=False)
        
        colores = {
            'Agua': '#1565c0',
            'Bosque al 2024': '#9cbe62',
            'Deforestacion del 2001 al 2024': '#ff0000',
            'No Bosque al 2000': '#e6c46a',
            'Sin informacion': '#b2b2b2'
        }
        
        import matplotlib.pyplot as plt
        import pandas as pd
        
        # ==========================================
        # GRÁFICO 1: Barras (Más amplio)
        # ==========================================
        plt.figure(figsize=(14, 7)) # Tamaño ampliado
        bars = plt.bar(
            area_df['NAME'],
            area_df['Area_ha'],
            color=[colores.get(n, '#000000') for n in area_df['NAME']],
            edgecolor='black'
        )
        
        for bar in bars:
            altura = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2, altura + max(area_df['Area_ha']) * 0.01,
                     f"{altura:,.2f} ha", ha='center', va='bottom', fontsize=11, fontweight='bold')
                     
        plt.title("Cobertura de Bosques y No Bosques al 2024", fontweight='bold', fontsize=16)
        plt.xlabel('Clase de cobertura', fontweight='bold', fontsize=12)
        plt.ylabel('Área (hectáreas)', fontweight='bold', fontsize=12)
        plt.xticks(rotation=15, ha='center', fontsize=11)
        plt.grid(axis='y', linestyle='--', alpha=0.4)
        plt.tight_layout()
        
        out_img = os.path.join(self.out_dir, 'grafico_cobertura_barras.jpg')
        plt.savefig(out_img, dpi=300, format='jpg', bbox_inches='tight')
        plt.close()

        # ==========================================
        # GRÁFICO 2: Torta (Pie Chart)
        # ==========================================
        plt.figure(figsize=(10, 8))
        
        # Filtrar valores mayores a 0 para que no salgan etiquetas encimadas
        pie_df = area_df[area_df['Area_ha'] > 0].copy()
        colores_pie = [colores.get(n, '#000000') for n in pie_df['NAME']]
        
        wedges, texts, autotexts = plt.pie(
            pie_df['Area_ha'],
            labels=pie_df['NAME'],
            colors=colores_pie,
            autopct='%1.1f%%',
            startangle=140,
            pctdistance=0.8,
            wedgeprops={'edgecolor': 'black', 'linewidth': 1, 'antialiased': True},
            textprops={'fontsize': 11, 'fontweight': 'bold'}
        )
        
        # Ajustar color del texto de porcentaje para mejor contraste
        plt.setp(autotexts, size=11, weight="bold", color="white")
        for autotext, color_name in zip(autotexts, pie_df['NAME']):
            if color_name in ['No Bosque al 2000', 'Sin informacion', 'Bosque al 2024']:
                autotext.set_color('black') # Letras oscuras en colores claros
                
        plt.title("Proporción de Cobertura al 2024", fontweight='bold', fontsize=16)
        plt.tight_layout()
        
        out_pie = os.path.join(self.out_dir, 'grafico_cobertura_torta.jpg')
        plt.savefig(out_pie, dpi=300, format='jpg', bbox_inches='tight')
        plt.close()
        
        # ==========================================
        # EXPORTAR TABLA CSV
        # ==========================================
        csv_path = os.path.join(self.out_dir, 'Tabla_resumen_bosque_2024.csv')
        total_area = area_df['Area_ha'].sum()
        
        export_df = area_df.copy()
        fila_total = pd.DataFrame([{'NAME': 'TOTAL', 'Area_ha': total_area}])
        
        # Usar concat en lugar de append (append está deprecado en versiones nuevas de pandas)
        export_df = pd.concat([export_df, fila_total], ignore_index=True)
        
        # Exportar como Excel o CSV de respaldo
        excel_path = os.path.join(self.out_dir, 'Tabla_resumen_bosque_2024.xlsx')
        export_df.rename(columns={'NAME': 'Cobertura', 'Area_ha': 'Area (ha)'}, inplace=True)
        try:
            export_df.to_excel(excel_path, index=False)
        except Exception:
            csv_path = os.path.join(self.out_dir, 'Tabla_resumen_bosque_2024.csv')
            export_df.to_csv(csv_path, index=False, encoding='utf-8')

    def generate_perdida_charts(self, gdf, shp_file=None):
        # Mapear año
        col_name = next((c for c in ['Anual', 'anual', 'DN', 'label'] if c in gdf.columns), None)
        if not col_name: raise KeyError("No se encontró columna válida (Anual, DN) en los datos.")
        
        # Asegurarnos de que el campo Anual contenga valores >= 2 (2001 en adelante)
        gdf = gdf[gdf[col_name] >= 2].copy()
        
        gdf['Anio'] = gdf[col_name] + 1999
        
        min_year = int(gdf['Anio'].min()) if not gdf.empty else 2001
        max_year = int(gdf['Anio'].max()) if not gdf.empty else 2024
        
        # Calcular área
        if gdf.crs and gdf.crs.is_geographic:
            gdf_proj = gdf.to_crs("EPSG:32718")
            gdf['area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
        else:
            gdf['area_ha'] = (gdf.geometry.area / 10000).round(4)
        
        # Clasificar área
        def clasificar(area):
            if area < 1: return '<1 ha'
            elif area < 5: return '1–5 ha'
            elif area < 50: return '5–50 ha'
            elif area < 500: return '50–500 ha'
            else: return '>500 ha'
            
        gdf['Clase'] = gdf['area_ha'].apply(clasificar)
        
        # Guardar cambios en el SHP/GPKG
        if shp_file:
            import os
            driver = 'GPKG' if shp_file.lower().endswith('.gpkg') else 'ESRI Shapefile'
            if os.path.exists(shp_file):
                try: os.remove(shp_file)
                except: pass
            gdf.to_file(shp_file, driver=driver, encoding='utf-8')
            
        import matplotlib.pyplot as plt
        import pandas as pd
        
        # 1. Gráfico Anual
        perdida_agrupado = gdf.groupby('Anio')['area_ha'].sum().reset_index()
        total_perdida = perdida_agrupado['area_ha'].sum()
        ymax = perdida_agrupado['area_ha'].max() * 1.15 if not perdida_agrupado.empty else 100
        
        plt.figure(figsize=(14, 7))
        bars = plt.bar(perdida_agrupado['Anio'], perdida_agrupado['area_ha'], color='red', edgecolor='black', width=0.6)
        
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
                    fontsize=11,
                    fontweight='bold',
                    color='white'
                )
                     
        # Añadir texto del total en la parte superior derecha
        plt.text(
            x=max_year - 1.5,  # año final ajustado a la izquierda
            y=ymax * 0.95,  # un poco por encima de la barra más alta
            s=f"Total de pérdida:\n{total_perdida:,.0f} ha",
            ha='right',
            va='top',
            fontsize=12,
            fontweight='bold',
            bbox=dict(facecolor='white', edgecolor='gray', boxstyle='round,pad=0.3')
        )
                 
        plt.ylim(0, ymax)
        plt.xlabel('Año', fontweight='bold', fontsize=12)
        plt.ylabel('Pérdida de bosque (hectáreas)', fontweight='bold', fontsize=12)
        plt.title(f'Pérdida anual de bosque ({min_year}–{max_year})', fontweight='bold', fontsize=15)
        plt.grid(axis='y', linestyle='--', alpha=0.4)
        
        plt.xticks(range(min_year, max_year + 1), rotation=45)
        plt.tight_layout()
        
        out_img1 = os.path.join(self.out_dir, 'perdida_bosque_anual.jpg')
        plt.savefig(out_img1, dpi=300, format='jpg', bbox_inches='tight')
        plt.close()
        
        # 2. Gráfico Apilado por Clase y Tabla CSV
        agrupar_clase = gdf.groupby(['Anio', 'Clase'])['area_ha'].sum().unstack(fill_value=0)
        
        orden = ['<1 ha', '1–5 ha', '5–50 ha', '50–500 ha', '>500 ha']
        
        # Asegurar todos los años y todas las clases
        # Asegurar todos los años y todas las clases
        agrupar_clase = agrupar_clase.reindex(list(range(min_year, max_year + 1)), fill_value=0)
        for col in orden:
            if col not in agrupar_clase.columns:
                agrupar_clase[col] = 0
                
        agrupar_clase = agrupar_clase[orden]
        
        # Generar tabla Excel (con fallback a CSV si falta librería)
        csv_df = agrupar_clase.copy()
        csv_df['TOTAL'] = csv_df.sum(axis=1)
        
        totales = csv_df.sum(axis=0).to_frame().T
        totales.index = ['TOTAL']
        
        csv_df = pd.concat([csv_df, totales])
        csv_df.index.name = 'Anio'
        csv_df = csv_df.reset_index()
        
        excel_path = os.path.join(self.out_dir, 'Tabla_resumen_perdida_Actualizado.xlsx')
        try:
            csv_df.to_excel(excel_path, index=False)
        except Exception:
            # Fallback a CSV si falla
            csv_fallback = os.path.join(self.out_dir, 'Tabla_resumen_perdida_Actualizado.csv')
            csv_df.to_csv(csv_fallback, index=False, encoding='utf-8')
        
        # Graficar
        colores_dict = {
            '<1 ha': '#f6942e',
            '1–5 ha': '#f6d32e',
            '5–50 ha': '#61be53',
            '50–500 ha': '#7c2d0e',
            '>500 ha': '#ec2dc9'
        }
        
        ax = agrupar_clase.plot(
            kind='bar', stacked=True, figsize=(15, 8),
            color=[colores_dict[c] for c in agrupar_clase.columns],
            edgecolor='black', width=0.75
        )
        
        # Ajustar ymax para dar mucho más espacio al texto sobre las barras
        sum_por_anio = agrupar_clase.sum(axis=1)
        ymax2 = sum_por_anio.max() * 1.35 if not sum_por_anio.empty else 100
        plt.ylim(0, ymax2)
        
        # Colocar la leyenda en la parte superior izquierda (dentro del gráfico) para evitar recortes
        plt.legend(title='Tamaño de Clase', loc='upper left', fontsize=11, edgecolor='black', title_fontsize=12)
        
        plt.title('Pérdida Anual de Bosque por Tamaño de Clase', fontweight='bold', fontsize=16)
        plt.xlabel('Año', fontweight='bold', fontsize=12)
        plt.ylabel('Pérdida (hectáreas)', fontweight='bold', fontsize=12)
        anios_lista = list(range(min_year, max_year + 1))
        plt.xticks(range(len(anios_lista)), anios_lista, rotation=45)
        plt.grid(axis='y', linestyle='--', alpha=0.5)
        
        # Añadir textos del total sobre cada barra apilada
        for i, total in enumerate(sum_por_anio):
            if total > 0:
                plt.text(i, total + (ymax2 * 0.01), f"{total:,.0f}",
                         ha='center', va='bottom', rotation=90, fontsize=10, fontweight='bold', color='black')
        
        plt.tight_layout()
        
        out_img2 = os.path.join(self.out_dir, 'perdida_apilada_por_tamano.jpg')
        plt.savefig(out_img2, dpi=300, format='jpg', bbox_inches='tight')
        plt.close()

    def generate_alerta_charts(self, gdf, shp_file=None):
        # Alertas están por día del año (1 a 365)
        col_name = next((c for c in ['dia', 'Dia', 'DIA', 'DN', 'label'] if c in gdf.columns), None)
        if not col_name: raise KeyError("No se encontró columna válida (dia, DN) en los datos.")
        
        # Filtrar valores válidos de día (1 a 366)
        gdf = gdf[(gdf[col_name] >= 1) & (gdf[col_name] <= 366)].copy()
        
        if gdf.crs and gdf.crs.is_geographic:
            gdf_proj = gdf.to_crs("EPSG:32718")
            gdf['area_ha'] = (gdf_proj.geometry.area / 10000).round(4)
        else:
            gdf['area_ha'] = (gdf.geometry.area / 10000).round(4)
            
        import pandas as pd
        import matplotlib.pyplot as plt
        from datetime import datetime, timedelta
        
        # Determinar el año de la alerta
        year_alerta = 2025 if "2025" in self.producto else 2026
        
        def juliano_a_fecha(dia_juliano):
            try:
                dia_int = int(float(dia_juliano))
                if dia_int < 1: dia_int = 1
                return datetime(year_alerta, 1, 1) + timedelta(days=dia_int - 1)
            except:
                return datetime(year_alerta, 1, 1)
                
        # Crear columna de fecha y convertir a Datetime
        fechas = pd.to_datetime(gdf[col_name].apply(juliano_a_fecha))
        
        # Nuevos campos para el vector
        gdf['Fecha'] = fechas.dt.strftime('%Y-%m-%d')
        
        meses_es = {1:'Enero', 2:'Febrero', 3:'Marzo', 4:'Abril', 5:'Mayo', 6:'Junio', 
                    7:'Julio', 8:'Agosto', 9:'Septiembre', 10:'Octubre', 11:'Noviembre', 12:'Diciembre'}
                    
        gdf['Mes_num'] = fechas.dt.month
        gdf['Mes'] = gdf['Mes_num'].map(meses_es)
        gdf['Anio'] = fechas.dt.year
        
        # Guardar en Shapefile/GPKG
        if shp_file:
            import os
            driver = 'GPKG' if shp_file.lower().endswith('.gpkg') else 'ESRI Shapefile'
            if os.path.exists(shp_file):
                try: os.remove(shp_file)
                except: pass
            gdf.to_file(shp_file, driver=driver, encoding='utf-8')
            
        # Graficar
        # Agrupar por mes, ordenando cronológicamente
        agrupado = gdf.groupby(['Mes_num', 'Mes'])['area_ha'].sum().reset_index().sort_values('Mes_num')
        
        total_alerta = agrupado['area_ha'].sum()
        
        # Exportar Excel de Alerta Temprana
        export_df = agrupado[['Mes', 'area_ha']].copy()
        fila_total = pd.DataFrame([{'Mes': 'TOTAL', 'area_ha': total_alerta}])
        export_df = pd.concat([export_df, fila_total], ignore_index=True)
        export_df.rename(columns={'area_ha': 'Area (ha)'}, inplace=True)
        
        try:
            excel_path = os.path.join(self.out_dir, f'Tabla_resumen_alerta_{year_alerta}.xlsx')
            export_df.to_excel(excel_path, index=False)
        except Exception:
            # Fallback a CSV
            csv_path = os.path.join(self.out_dir, f'Tabla_resumen_alerta_{year_alerta}.csv')
            export_df.to_csv(csv_path, index=False, encoding='utf-8')
        
        plt.figure(figsize=(14, 7))
        ymax = agrupado['area_ha'].max() * 1.25 if not agrupado.empty else 100
        
        bars = plt.bar(agrupado['Mes'], agrupado['area_ha'], color='#d35400', edgecolor='black', width=0.6)
        
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                plt.text(bar.get_x() + bar.get_width() / 2, height + (ymax * 0.01), f"{height:,.2f}",
                         ha='center', va='bottom', fontsize=11, fontweight='bold', color='black')
                         
        # Añadir texto del total
        x_pos = len(agrupado) - 1 if not agrupado.empty else 0
        plt.text(x=x_pos, y=ymax * 0.95, 
                 s=f"Total de pérdida:\n{total_alerta:,.2f} ha",
                 ha='right', va='top', fontsize=12, fontweight='bold', 
                 bbox=dict(facecolor='white', edgecolor='gray', boxstyle='round,pad=0.3'))
                 
        plt.ylim(0, ymax)
        plt.title(f'Pérdida de bosque por mes (Alerta temprana {year_alerta} - GEOBOSQUES)', fontweight='bold', fontsize=16)
        plt.xlabel('Mes', fontweight='bold', fontsize=12)
        plt.ylabel('Pérdida (hectáreas)', fontweight='bold', fontsize=12)
        plt.xticks(rotation=45, ha='right', fontsize=11)
        plt.grid(axis='y', linestyle='--', alpha=0.5)
        plt.tight_layout()
        
        out_img = os.path.join(self.out_dir, 'alerta_temprana_mensual.jpg')
        plt.savefig(out_img, dpi=300, format='jpg', bbox_inches='tight')
        plt.close()
