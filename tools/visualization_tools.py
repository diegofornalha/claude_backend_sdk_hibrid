"""
Visualization Tools - Gráficos e mapas SEM AWS

Substitui AgentCore Code Interpreter + S3 por execução local com matplotlib/folium.
Salva arquivos em /static/charts/ e /static/maps/ localmente.
"""

import os
import uuid
import base64
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Diretórios locais para storage
CHARTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "charts")
MAPS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "maps")

# Garantir que diretórios existem
os.makedirs(CHARTS_DIR, exist_ok=True)
os.makedirs(MAPS_DIR, exist_ok=True)


def generate_visualization(data: Dict[str, Any], chart_type: str = "bar") -> Dict[str, Any]:
    """
    Gera visualização de dados usando matplotlib - SEM AgentCore

    Args:
        data: Dados para visualizar (ex: {"labels": [...], "values": [...]})
        chart_type: Tipo de gráfico (bar, line, pie)

    Returns:
        Dict com image_url para acessar o gráfico salvo localmente
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use('Agg')  # Backend não-interativo

        # Gerar nome único
        chart_id = str(uuid.uuid4())[:8]
        filename = f"chart_{chart_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = os.path.join(CHARTS_DIR, filename)

        # Extrair dados
        labels = data.get('labels', [])
        values = data.get('values', [])
        title = data.get('title', 'Data Visualization')
        xlabel = data.get('xlabel', 'Category')
        ylabel = data.get('ylabel', 'Count')

        # Criar figura
        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "bar":
            ax.bar(labels, values, color='#4CAF50')
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.set_title(title)
            plt.xticks(rotation=45, ha='right')

        elif chart_type == "line":
            ax.plot(range(len(values)), values, marker='o', linewidth=2, color='#2196F3', markersize=6)
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha='right')
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.set_title(title)
            ax.grid(True, alpha=0.3)

        elif chart_type == "pie":
            colors = ['#4CAF50', '#2196F3', '#FFC107', '#FF5722', '#9C27B0', '#00BCD4', '#FF9800']
            ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=90, colors=colors)
            ax.set_title(title)

        else:
            plt.close(fig)
            return {
                "success": False,
                "error": f"Unsupported chart type: {chart_type}"
            }

        # Salvar
        plt.tight_layout()
        plt.savefig(filepath, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)

        # URL local (relativo ao servidor)
        image_url = f"/static/charts/{filename}"

        logger.info(f"Generated {chart_type} chart: {filepath}")

        return {
            "success": True,
            "image_url": image_url,
            "filename": filename,
            "chart_type": chart_type
        }

    except Exception as e:
        logger.error(f"Error generating visualization: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def create_map_visualization(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cria mapa interativo usando folium - SEM S3

    Args:
        data: Dados do mapa (ex: {"locations": [...], "center": [lat, lon]})

    Returns:
        Dict com map_url para acessar o mapa salvo localmente
    """
    try:
        import folium
        from folium.plugins import MarkerCluster

        # Gerar nome único
        map_id = str(uuid.uuid4())[:8]
        filename = f"map_{map_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = os.path.join(MAPS_DIR, filename)

        # Extrair dados
        locations = data.get('locations', [])
        center = data.get('center', [-8.556, 125.560])  # Default: Timor-Leste
        zoom = data.get('zoom', 12)
        title = data.get('title', 'Waste Reports Map')

        # Criar mapa
        m = folium.Map(
            location=center,
            zoom_start=zoom,
            tiles='OpenStreetMap'
        )

        # Adicionar cluster de marcadores
        marker_cluster = MarkerCluster().add_to(m)

        # Adicionar marcadores
        for loc in locations:
            lat = loc.get('latitude')
            lon = loc.get('longitude')
            popup_text = loc.get('description', 'Waste Report')
            waste_type = loc.get('waste_type', 'Unknown')
            severity = loc.get('severity_score', 0)

            # Cor baseada em severidade
            if severity >= 8:
                color = 'red'
            elif severity >= 5:
                color = 'orange'
            else:
                color = 'green'

            folium.Marker(
                location=[lat, lon],
                popup=f"<b>{waste_type}</b><br>{popup_text}<br>Severity: {severity}/10",
                tooltip=waste_type,
                icon=folium.Icon(color=color, icon='trash', prefix='fa')
            ).add_to(marker_cluster)

        # Adicionar título
        title_html = f'''
        <div style="position: fixed;
                    top: 10px; left: 50px; width: 300px; height: 50px;
                    background-color: white; border:2px solid grey; z-index:9999;
                    font-size:16px; font-weight: bold; padding: 10px">
            {title}
        </div>
        '''
        m.get_root().html.add_child(folium.Element(title_html))

        # Salvar
        m.save(filepath)

        # URL local
        map_url = f"/static/maps/{filename}"

        logger.info(f"Generated map with {len(locations)} locations: {filepath}")

        return {
            "success": True,
            "map_url": map_url,
            "filename": filename,
            "locations_count": len(locations)
        }

    except Exception as e:
        logger.error(f"Error creating map: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# Limpar arquivos antigos (chamar periodicamente)
def cleanup_old_files(max_age_hours: int = 24):
    """Remove arquivos com mais de max_age_hours"""
    import time

    now = time.time()
    max_age_seconds = max_age_hours * 3600

    for directory in [CHARTS_DIR, MAPS_DIR]:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                file_age = now - os.path.getmtime(filepath)
                if file_age > max_age_seconds:
                    try:
                        os.remove(filepath)
                        logger.info(f"Cleaned up old file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
