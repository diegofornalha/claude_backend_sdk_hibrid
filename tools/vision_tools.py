"""
Vision Tools - Análise de imagens usando Claude Code CLI

Usa Claude Code CLI local via subprocess para análise de imagens.
SEM necessidade de API key - usa autenticação do Claude Code CLI.
"""

import json
import logging
import base64
import subprocess
import tempfile
import os
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def analyze_waste_image_direct(
    image_base64: str = "",
    image_path: str = "",
    latitude: float = 0.0,
    longitude: float = 0.0,
    description: str = ""
) -> Dict:
    """
    Analisa imagem de resíduo usando Claude Code CLI

    Usa subprocess para chamar o Claude Code CLI com a imagem.

    Args:
        image_base64: Imagem em base64 (opcional se image_path fornecido)
        image_path: Caminho local da imagem (opcional se image_base64 fornecido)
        latitude: Latitude do local
        longitude: Longitude do local
        description: Descrição fornecida pelo usuário

    Returns:
        Dict com análise estruturada
    """
    temp_image_path = None

    try:
        # Determinar caminho da imagem
        if image_path and os.path.exists(image_path):
            actual_image_path = image_path
        elif image_base64:
            # Salvar imagem base64 em arquivo temporário
            # Remover prefixo data:image/... se existir
            if image_base64.startswith('data:'):
                image_base64 = image_base64.split(',', 1)[1]

            image_data = base64.b64decode(image_base64)

            # Criar arquivo temporário
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
                f.write(image_data)
                temp_image_path = f.name
                actual_image_path = temp_image_path
        else:
            logger.error("No image provided (neither base64 nor path)")
            return _error_result("No image provided")

        logger.info(f"Analyzing image with Claude Code CLI: {actual_image_path}")

        # Prompt para análise
        prompt = f"""Analyze this waste/garbage image and provide a JSON response.

Context:
- Location: Latitude {latitude}, Longitude {longitude}
- User description: "{description}"

Analyze the image and respond with ONLY this JSON format (no other text):
{{
    "is_waste": true or false,
    "waste_type": "Plastic/Paper/Glass/Metal/Organic/Electronic/Textile/Mixed/Hazardous/Construction/Other/Not Garbage",
    "waste_subtypes": ["specific items found"],
    "severity_score": 1-10,
    "priority_level": "Low/Medium/High/Critical",
    "description": "What you see in the image",
    "environmental_impact": "Environmental impact assessment",
    "recommended_action": "Suggested cleanup method",
    "volume_estimate": "Small/Medium/Large/Very Large",
    "confidence": 0.0-1.0
}}

If the image does NOT contain waste/garbage, set is_waste to false and waste_type to "Not Garbage".
Respond with ONLY valid JSON, nothing else."""

        # Chamar Claude Code CLI
        result = subprocess.run(
            ['claude', '-p', prompt, actual_image_path],
            capture_output=True,
            text=True,
            timeout=120  # 2 minutos timeout
        )

        if result.returncode != 0:
            logger.error(f"Claude CLI error: {result.stderr}")
            return _error_result(f"Claude CLI failed: {result.stderr}")

        # Parsear resposta JSON
        response_text = result.stdout.strip()
        logger.info(f"Claude CLI response: {response_text[:500]}...")

        # Tentar extrair JSON da resposta
        analysis = _extract_json(response_text)

        if analysis:
            analysis['analyzed_at'] = datetime.now().isoformat()
            analysis['analysis_method'] = 'Claude Code CLI'
            logger.info(f"Analysis complete: {analysis.get('waste_type', 'Unknown')}")
            return analysis
        else:
            logger.error(f"Failed to parse JSON from response: {response_text}")
            return _error_result("Failed to parse analysis response")

    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timeout")
        return _error_result("Analysis timeout")
    except FileNotFoundError:
        logger.error("Claude CLI not found - is it installed?")
        return _error_result("Claude CLI not installed")
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")
        return _error_result(str(e))
    finally:
        # Limpar arquivo temporário
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.unlink(temp_image_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")


def _extract_json(text: str) -> Optional[Dict]:
    """Extrai JSON de uma string que pode conter texto adicional"""
    import re

    # Tentar parsear diretamente
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Procurar por JSON em blocos de código
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
        r'\{[\s\S]*\}'
    ]

    for pattern in json_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue

    return None


def _error_result(message: str) -> Dict:
    """Retorna resultado de erro padronizado"""
    return {
        "is_waste": False,
        "waste_type": "Unknown",
        "waste_subtypes": [],
        "severity_score": 0,
        "priority_level": "Low",
        "description": f"Analysis failed: {message}",
        "environmental_impact": "Unknown",
        "recommended_action": "Manual review required",
        "volume_estimate": "Unknown",
        "confidence": 0.0,
        "analyzed_at": datetime.now().isoformat(),
        "analysis_method": "Error",
        "error": message
    }


# Função para teste rápido
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python vision_tools.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    result = analyze_waste_image_direct(image_path=image_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
