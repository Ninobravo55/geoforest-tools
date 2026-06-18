import os
import matplotlib
matplotlib.use("Agg")  # backend headless, thread-safe en worker de Processing
import matplotlib.pyplot as plt

class EarlyWarningCharts:
    @staticmethod
    def generate_summary_and_chart(gdf, out_folder, base_filename, producto_nombre):
        if gdf.empty:
            return

        # Agrupar por mes
        resumen = gdf.groupby('num_mes').agg(
            area_ha=('area_ha', 'sum'),
            cantidad_poligonos=('num_mes', 'count')
        ).reset_index()

        meses_map = {1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
                     7: 'Julio', 8: 'Agosto', 9: 'Setiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'}
        
        resumen['mes'] = resumen['num_mes'].map(meses_map)
        resumen = resumen.sort_values('num_mes')
        
        # Guardar a CSV
        csv_path = os.path.join(out_folder, f"{base_filename}_resumen.csv")
        resumen[['num_mes', 'mes', 'area_ha', 'cantidad_poligonos']].to_csv(csv_path, index=False)

        # Generar Gráfico
        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.bar(resumen['mes'], resumen['area_ha'], color='indianred')
        
        ax.set_xlabel('Meses')
        ax.set_ylabel('Área de Alerta (Hectáreas)')
        ax.set_title(f'Pérdida de bosque mensual - {producto_nombre}')
        
        # Etiquetas encima de las barras
        for bar in bars:
            yval = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2.0, yval, f'{yval:.1f} ha', ha='center', va='bottom')
            
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        png_path = os.path.join(out_folder, f"{base_filename}_grafico.png")
        plt.savefig(png_path, dpi=300)
        plt.close(fig)
