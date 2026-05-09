# AI Services Integration Guide
## Extending AI Station with OpenHands, Gemini Pro & Cloud AI

---

## Integration Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI STATION - CLOUD MODE                      │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  DeepSeek    │  │   Gemini     │  │    OpenAI/Claude     │   │
│  │  (Cloud)     │  │   (Cloud)    │  │      (Cloud)         │   │
│  │deepseek-chat │  │gemini-2.5-pro│  │   gpt-4o/claude-3.7  │   │
│  │ ds-reasoner  │  │  (multimodal)│  │                      │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
│         │                  │                     │              │
│         └──────────────────┼─────────────────────┘              │
│                            │                                      │
│              ┌─────────────┴─────────────┐                      │
│              │   LLM Router / Load Balancer│                      │
│              │   (CrewAI Fallback Chain)   │                      │
│              └─────────────┬─────────────┘                      │
│                            │                                      │
│         ┌──────────────────┼──────────────────┐                 │
│         ▼                  ▼                  ▼                 │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐         │
│  │   CrewAI    │   │  OpenHands   │   │ Specialized  │         │
│  │   Flows     │   │   Runtime    │   │   Tools      │         │
│  │             │   │              │   │              │         │
│  │ • Architect │   │ • Code Edit  │   │ • Codeium    │         │
│  │ • Coder     │   │ • Debug      │   │ • Copilot    │         │
│  │ • Healer    │   │ • Test Gen   │   │ • Perplexity │         │
│  │ • Reviewer  │   │ • Refactor   │   │ • Grok       │         │
│  └─────────────┘   └──────────────┘   └──────────────┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. OpenHands Integration

### What is OpenHands?
OpenHands is an open-source AI software development agent that can edit code, run commands, and debug autonomously.

### Integration Options

#### Option A: OpenHands as Alternative Coder Agent
Replace or supplement the CrewAI Coder with OpenHands runtime:

```python
# autonomous_pipeline.py - Add OpenHands integration

import asyncio
import aiohttp
from typing import Optional

class OpenHandsClient:
    """Client for OpenHands runtime integration"""
    
    def __init__(self, base_url: str = "http://localhost:3000"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
    
    async def initialize_runtime(self, workspace_dir: str):
        """Initialize OpenHands runtime with workspace"""
        async with self.session.post(
            f"{self.base_url}/api/initialize",
            json={"workspace": workspace_dir}
        ) as resp:
            return await resp.json()
    
    async def execute_task(
        self, 
        instruction: str, 
        files: list[str],
        timeout: int = 300
    ) -> dict:
        """
        Send coding task to OpenHands
        
        Example instruction:
        "Implement a function that calculates fibonacci with memoization. 
         Create tests in test_fibonacci.py"
        """
        payload = {
            "instruction": instruction,
            "files": files,
            "timeout": timeout,
            "llm_config": {
                "model": "deepseek/deepseek-chat",  # Cloud API
                "base_url": "https://api.deepseek.com/v1",
                "api_key": os.getenv("DEEPSEEK_API_KEY", "")
            }
        }
        
        async with self.session.post(
            f"{self.base_url}/api/tasks",
            json=payload
        ) as resp:
            result = await resp.json()
            return {
                "success": result.get("status") == "success",
                "files_modified": result.get("files_modified", []),
                "diff": result.get("diff", ""),
                "tests_run": result.get("tests_run", []),
                "explanation": result.get("explanation", "")
            }

# Usage in CrewAI Flow:
class AutonomousPipelineFlow(Flow):
    
    @listen(design_architecture)
    async def implement_with_openhands(self, architecture: str):
        """Use OpenHands for implementation instead of CrewAI Coder"""
        
        async with OpenHandsClient("http://localhost:3000") as client:
            await client.initialize_runtime(self.workspace_dir)
            
            result = await client.execute_task(
                instruction=f"""
                Implement this architecture:
                {architecture}
                
                Requirements:
                - Write clean, tested Python code
                - Follow the file structure in the architecture
                - Run tests to verify implementation
                - Fix any issues automatically
                """,
                files=[],  # OpenHands will create new files
                timeout=600
            )
            
            if result["success"]:
                # Parse result["files_modified"] to update pipeline state
                return result
            else:
                # Fallback to CrewAI Coder
                return await self.implement_code(architecture)
```

#### Option B: OpenHands as Code Review Agent
Add OpenHands as a specialized reviewer that can actually execute and verify code:

```python
class OpenHandsReviewer:
    """OpenHands-powered code reviewer that executes tests"""
    
    async def review_and_verify(self, code_files: dict[str, str]) -> dict:
        """Review code and actually run it to verify"""
        
        # Write code to temp workspace
        temp_dir = tempfile.mkdtemp()
        for path, content in code_files.items():
            with open(f"{temp_dir}/{path}", 'w') as f:
                f.write(content)
        
        # Ask OpenHands to review and test
        review_result = await self.openhands.execute_task(
            instruction="""
            Review this code for:
            1. Security vulnerabilities
            2. Performance issues  
            3. Code quality
            4. Run all tests and verify they pass
            5. Check edge cases
            
            Return a detailed report with any fixes applied.
            """,
            files=list(code_files.keys()),
            timeout=300
        )
        
        return review_result
```

#### Option C: Hybrid Mode - OpenHands + CrewAI
Use both systems together with task routing:

```python
class HybridAgentRouter:
    """Routes tasks to either CrewAI or OpenHands based on complexity"""
    
    def __init__(self, crewai_flow, openhands_client):
        self.crewai = crewai_flow
        self.openhands = openhands_client
        
    async def execute_coding_task(self, requirements: str, complexity: str):
        """Route to appropriate agent based on complexity"""
        
        if complexity == "simple":
            # Fast path with CrewAI (local, no overhead)
            return await self.crewai.implement_code(requirements)
            
        elif complexity == "complex":
            # Deep reasoning with OpenHands (iterative debugging)
            return await self.openhands.execute_task(
                instruction=requirements,
                files=[],
                timeout=900
            )
            
        elif complexity == "debug":
            # Self-healing through OpenHands execution
            return await self.openhands.execute_task(
                instruction=f"Debug and fix: {requirements}",
                files=[],  # Will scan workspace
                timeout=600,
                mode="debug"
            )
```

### Setup Script Addition

Add to `setup_inference.sh` after CrewAI setup:

```bash
# Install OpenHands (optional)
install_openhands() {
    log_info "Installing OpenHands (optional integration)..."
    
    # Clone OpenHands
    local openhands_dir="$HOME/openhands"
    if [[ ! -d "$openhands_dir" ]]; then
        git clone https://github.com/All-Hands-AI/OpenHands.git "$openhands_dir"
    fi
    
    # Create virtual environment for OpenHands
    cd "$openhands_dir"
    python3 -m venv venv
    source venv/bin/activate
    
    # Install dependencies
    pip install -e .
    
    # Build frontend
    make build
    
    # Create launch script
    cat > "$HOME/ai_station/scripts/start_openhands.sh" <<'EOF'
#!/bin/zsh
cd ~/openhands
source venv/bin/activate
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_API_KEY="$DEEPSEEK_API_KEY"
python -m openhands.server.listen --port 3000
EOF
    chmod +x "$HOME/ai_station/scripts/start_openhands.sh"
    
    log_info "OpenHands installed. Start with: start_openhands.sh"
}
```

---

## 2. Gemini AI Pro Integration

### Gemini as Cloud Fallback
Use Gemini as fallback when DeepSeek is unavailable or for multimodal tasks:

```python
# llm_config.py - Extended LLM configuration

import os
from google import genai
from google.genai import types
from crewai import LLM

class LLMRouter:
    """Routes LLM requests with fallback chain"""
    
    def __init__(self):
        self.deepseek_base = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
        self.deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')
        self.openai_key = os.getenv('OPENAI_API_KEY', '')
        self.gemini_key = os.getenv('GEMINI_API_KEY')
        self.genai_client = None
        
        if self.gemini_key:
            self.genai_client = genai.Client(api_key=self.gemini_key)
    
    def get_llm(self, task_type: str = "general", priority: str = "normal") -> LLM:
        """
        Get appropriate LLM based on task requirements
        
        Task types:
        - "coding": Complex code generation (DeepSeek-Chat or Gemini 2.5 Pro)
        - "healing": Quick debugging (DeepSeek-Chat / GPT-4o-mini)
        - "reasoning": Deep analysis (Gemini 2.5 Pro)
        - "vision": Multimodal (Gemini 2.5 Pro Vision)
        - "documentation": Long-form (Gemini with large context)
        """
        
        # Priority routing
        if priority == "critical" and self.genai_client:
            # Critical tasks use cloud for reliability
            return self._gemini_llm("gemini-2.5-pro-preview-03-25")
        
        # Task-based routing
        routing_map = {
            "coding": self._deepseek_coding_llm,
            "healing": self._deepseek_fast_llm,
            "reasoning": self._gemini_llm if self.genai_client else self._deepseek_coding_llm,
            "vision": self._gemini_vision_llm if self.genai_client else None,
            "documentation": self._gemini_llm if self.genai_client else self._deepseek_coding_llm,
        }
        
        llm_factory = routing_map.get(task_type, self._deepseek_coding_llm)
        return llm_factory("gemini-2.5-pro-preview-03-25" if task_type in ["reasoning", "vision", "documentation"] else "")
    
    def _deepseek_coding_llm(self, _) -> LLM:
        return LLM(
            model="deepseek/deepseek-chat",
            base_url=self.deepseek_base,
            api_key=self.deepseek_key,
            temperature=0.3,
        )
    
    def _deepseek_fast_llm(self, _) -> LLM:
        return LLM(
            model="deepseek/deepseek-chat",
            base_url=self.deepseek_base,
            api_key=self.deepseek_key,
            temperature=0.1,
        )
    
    def _gemini_llm(self, model: str) -> LLM:
        """Gemini Pro for advanced reasoning"""
        return LLM(
            model=f"gemini/{model}",
            api_key=self.gemini_key,
            temperature=0.2,
            max_tokens=8192,
        )
    
    def _gemini_vision_llm(self, model: str) -> LLM:
        """Gemini Pro Vision for UI/design analysis"""
        return LLM(
            model="gemini/gemini-2.5-pro-vision",
            api_key=self.gemini_key,
            temperature=0.1,
            max_tokens=4096,
        )
    
    async def analyze_ui_screenshot(self, image_path: str, requirements: str) -> str:
        """Use Gemini Vision to analyze UI mockups/screenshots"""
        
        if not self.genai_client:
            return "Gemini Vision not configured"
        
        with open(image_path, 'rb') as f:
            image_data = f.read()
        
        response = self.genai_client.models.generate_content(
            model="gemini-2.5-pro-vision",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=f"""Analyze this UI screenshot for these requirements:
{requirements}

Provide:
1. Implementation approach (framework, components)
2. CSS/styling recommendations
3. Responsive design considerations
4. Accessibility issues to address"""
                        ),
                        types.Part.from_bytes(data=image_data, mime_type="image/png")
                    ]
                )
            ]
        )
        
        return response.text

# CrewAI Agent with Gemini fallback
class GeminiEnhancedArchitect:
    """Architect agent that uses Gemini for complex reasoning"""
    
    def __init__(self, llm_router: LLMRouter):
        self.llm_router = llm_router
        
    def create_agent(self, task_complexity: str = "normal") -> Agent:
        """Create agent with appropriate LLM"""
        
        llm = self.llm_router.get_llm(
            task_type="reasoning" if task_complexity == "complex" else "coding",
            priority="normal"
        )
        
        return Agent(
            role="Software Architect",
            goal="Design robust, scalable software architecture",
            backstory="""Expert architect. For complex distributed systems, 
            leverages advanced reasoning capabilities.""",
            llm=llm,
            verbose=True,
        )
```

### Gemini Integration in Autonomous Pipeline

```python
# Add to autonomous_pipeline.py - Gemini enhancement

class GeminiEnhancedFlow(AutonomousPipelineFlow):
    """Extended flow with Gemini for advanced tasks"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm_router = LLMRouter()
    
    @listen(design_architecture)
    async def design_with_gemini_fallback(self, requirements: str) -> str:
        """Use Gemini for complex architecture, fallback to local"""
        
        # Detect complexity
        complexity = "complex" if any(kw in requirements.lower() for kw in [
            "microservices", "distributed", "scalable", "high availability",
            "kubernetes", "docker", "async", "concurrent"
        ]) else "normal"
        
        agent = self.llm_router.create_agent(task_complexity=complexity)
        
        task = Task(
            description=f"Design architecture for: {requirements}",
            expected_output="Detailed architecture document",
            agent=agent,
        )
        
        crew = Crew(agents=[agent], tasks=[task], verbose=True)
        return str(crew.kickoff())
    
    async def analyze_release_notes(self, release_content: str) -> str:
        """Use Gemini for professional documentation generation"""
        
        llm = self.llm_router.get_llm(task_type="documentation")
        
        prompt = f"""Generate professional release notes for this software release:

{release_content}

Format as markdown with:
- Executive Summary
- New Features
- Improvements
- Bug Fixes
- Breaking Changes (if any)
- Migration Guide (if needed)
"""
        
        response = llm.call(prompt)
        return response
```

### Environment Configuration

Add to `.env.example`:

```bash
# Gemini AI Pro Integration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-pro-preview-03-25

# LLM Routing Configuration
LLM_PRIMARY=deepseek          # deepseek, openai, gemini
LLM_FALLBACK=gemini           # fallback when primary fails
LLM_VISION=gemini             # for image/ui analysis
CLOUD_LLM_TIMEOUT=30          # timeout for cloud requests (seconds)

# Rate Limiting
GEMINI_RATE_LIMIT=60          # requests per minute
DEEPSEEK_RATE_LIMIT=500       # requests per minute
```

---

## 3. Additional AI Service Integrations

### 3.1 Codeium / Continue.dev (VS Code Extension)

For MacBook Air (Dev Node) - Lightweight AI code completion:

```bash
# setup_dev_node.sh addition

install_codeium() {
    log_info "Configuring Codeium integration..."
    
    # Codeium works via browser extension and VS Code/Cursor
    # Just need to document the setup
    
    cat > "$HOME/ai_station/docs/codeium_setup.md" <<'EOF'
# Codeium Integration

1. Install Codeium extension in Cursor:
   - Open Cursor → Extensions → Search "Codeium"
   - Install "Codeium: Free AI Code Completion"
   
2. Authenticate:
   - Click Codeium icon in sidebar
   - Sign in with GitHub/Google
   - Free tier: Unlimited autocomplete

3. For self-hosted (enterprise):
   - Codeium can connect to your own LLM endpoint
   - Point to your cloud LLM endpoint (e.g., https://api.deepseek.com/v1)

4. Features:
   - Inline code completion
   - Chat interface (alternative to Telegram)
   - Code explanation on hover
EOF

    log_info "Codeium setup guide created"
}
```

### 3.2 Perplexity AI (Research & Documentation)

For researching best practices:

```python
# research_tools.py

import os
import requests
from typing import List, Dict

class PerplexityResearcher:
    """Perplexity AI for technical research"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv('PERPLEXITY_API_KEY')
        self.base_url = "https://api.perplexity.ai"
    
    async def research_best_practices(
        self, 
        technology: str, 
        context: str
    ) -> Dict[str, List[str]]:
        """Research best practices for a technology"""
        
        queries = [
            f"What are the best practices for {technology} in 2026?",
            f"Common security vulnerabilities in {technology}",
            f"Performance optimization tips for {technology}",
        ]
        
        results = {}
        for query in queries:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": "You are a technical research assistant."},
                        {"role": "user", "content": query}
                    ]
                }
            )
            results[query] = response.json()['choices'][0]['message']['content']
        
        return results
    
    async def research_for_architecture(
        self, 
        requirements: str
    ) -> str:
        """Research architecture patterns before design"""
        
        research = await self.research_best_practices(
            technology="microservices",  # extracted from requirements
            context=requirements
        )
        
        # Compile into prompt for architect agent
        return f"""Research findings:
{json.dumps(research, indent=2)}

Use these insights in your architecture design.
"""

# Usage in pipeline:
@listen(fetch_requirements)
async def research_before_design(self, requirements: str) -> str:
    """Research current best practices before architecture"""
    
    if os.getenv('PERPLEXITY_API_KEY'):
        researcher = PerplexityResearcher()
        research = await researcher.research_for_architecture(requirements)
        
        # Pass research to architect agent
        return f"{requirements}\n\n{research}"
    
    return requirements
```

### 3.3 Hugging Face Inference API

For specialized models:

```python
# huggingface_integration.py

from huggingface_hub import InferenceClient
import os

class HuggingFaceSpecialist:
    """Use HF Inference API for specialized tasks"""
    
    def __init__(self, token: str = None):
        self.token = token or os.getenv('HF_API_TOKEN')
        self.client = InferenceClient(token=self.token)
    
    async def security_audit(self, code: str) -> dict:
        """Use specialized security model"""
        
        # Example: Use a security-focused model
        result = self.client.text_generation(
            model="meta-llama/CodeLlama-7b-hf",  # or security-specific model
            inputs=f"""Analyze this code for security vulnerabilities:
```python
{code}
```

List any:
1. SQL injection risks
2. XSS vulnerabilities
3. Buffer overflows
4. Insecure dependencies
5. Hardcoded secrets""",
            parameters={"max_new_tokens": 500, "temperature": 0.1}
        )
        
        return {"security_report": result}
    
    async def generate_tests(self, function_code: str) -> str:
        """Generate comprehensive test cases"""
        
        result = self.client.text_generation(
            model="Salesforce/codegen-16B-mono",
            inputs=f"""Generate pytest test cases for this function:
```python
{function_code}
```

Include tests for:
- Happy path
- Edge cases (empty input, None, large values)
- Error conditions
- Boundary values""",
            parameters={"max_new_tokens": 1000, "temperature": 0.2}
        )
        
        return result
```

### 3.4 LM Studio / LocalAI (Optional Local Fallback)

For local model management if you add GPU hardware later:

```bash
# Optional — only needed if running models locally

install_lmstudio() {
    log_info "Installing LM Studio (optional local fallback)..."
    
    # Download LM Studio
    local dmg_url="https://installers.lmstudio.ai/macos/arm64/0.3.5-9/LM-Studio-0.3.5-9-arm64.dmg"
    local dmg_path="/tmp/LMStudio.dmg"
    
    curl -L "$dmg_url" -o "$dmg_path"
    hdiutil attach "$dmg_path"
    cp -R "/Volumes/LM Studio/LM Studio.app" /Applications/
    hdiutil detach "/Volumes/LM Studio"
    
    # Configure local API server
    # LM Studio exposes OpenAI-compatible API at localhost:1234
    
    log_info "LM Studio installed. Start and enable 'Local Server' in settings."
    log_info "API endpoint: http://localhost:1234/v1"
}

# CrewAI can use LM Studio via OpenAI-compatible API:
# LLM(model="openai/llama-3.1-8b", base_url="http://localhost:1234/v1", api_key="lm-studio")
```

---

## 4. Unified AI Configuration

### Extended .env.example

```bash
# =============================================================================
# AI SERVICE INTEGRATIONS - Extended Configuration
# =============================================================================

# --- DeepSeek (Primary Cloud LLM) ---
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_CODING_MODEL=deepseek-chat
DEEPSEEK_JUDGE_MODEL=deepseek-reasoner

# --- Google Gemini (Cloud Fallback) ---
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-pro-preview-03-25
GEMINI_VISION_MODEL=gemini-2.5-pro-vision
ENABLE_GEMINI_FALLBACK=true

# --- OpenAI (Alternative Cloud) ---
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o
ENABLE_OPENAI_FALLBACK=false

# --- Anthropic Claude (Alternative Cloud) ---
ANTHROPIC_API_KEY=your_anthropic_key
CLAUDE_MODEL=claude-3-7-sonnet-20250219
ENABLE_CLAUDE_FALLBACK=false

# --- OpenHands Integration ---
ENABLE_OPENHANDS=true
OPENHANDS_URL=http://localhost:3000
OPENHANDS_DEFAULT_MODEL=deepseek/deepseek-chat

# --- Perplexity AI (Research) ---
PERPLEXITY_API_KEY=your_perplexity_key
PERPLEXITY_MODEL=sonar-pro

# --- Hugging Face (Specialized Models) ---
HF_API_TOKEN=your_hf_token
HF_INFERENCE_ENDPOINT=https://api-inference.huggingface.co

# --- LM Studio (Alternative Local) ---
LMSTUDIO_ENABLED=false
LMSTUDIO_URL=http://localhost:1234/v1

# --- Codeium ---
CODEIUM_ENABLED=true
CODEIUM_API_KEY=your_codeium_key

# --- Routing Logic ---
LLM_ROUTING_STRATEGY=smart  # smart, local-only, cloud-only, round-robin
CLOUD_LLM_TIMEOUT=30
LOCAL_LLM_TIMEOUT=120
MAX_LLM_RETRIES=3

# --- Rate Limiting ---
CLOUD_RATE_LIMIT_PER_MINUTE=60
LOCAL_RATE_LIMIT_PER_MINUTE=1000
```

### AI Router Service

Create `ai_router.py` for intelligent LLM selection:

```python
"""Central AI service router with fallback and load balancing"""

import asyncio
import random
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

class LLMProvider(Enum):
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    OPENAI = "openai"
    CLAUDE = "claude"
    OPENHANDS = "openhands"

@dataclass
class LLMHealth:
    provider: LLMProvider
    healthy: bool
    latency_ms: float
    last_error: Optional[str] = None
    request_count: int = 0

class AIProviderRouter:
    """Routes requests to healthiest AI provider with circuit breaker pattern"""
    
    def __init__(self):
        self.health_status: Dict[LLMProvider, LLMHealth] = {}
        self.circuit_breakers: Dict[LLMProvider, bool] = {}
        
    async def route_request(
        self, 
        task: str,
        required_capabilities: List[str] = None
    ) -> str:
        """
        Route to best available provider
        
        Capabilities:
        - "coding": Code generation
        - "vision": Image analysis
        - "long_context": 100k+ tokens
        - "fast": Low latency
        - "reasoning": Complex logic
        """
        
        candidates = self._get_candidates(required_capabilities)
        
        for provider in candidates:
            if self.circuit_breakers.get(provider, False):
                continue  # Skip broken circuits
                
            try:
                result = await self._execute_with_provider(provider, task)
                self._record_success(provider)
                return result
                
            except Exception as e:
                self._record_failure(provider, str(e))
                continue
        
        raise RuntimeError("All AI providers failed")
    
    def _get_candidates(self, capabilities: List[str]) -> List[LLMProvider]:
        """Score and rank providers by capability match"""
        
        capability_map = {
            "vision": [LLMProvider.GEMINI],
            "long_context": [LLMProvider.GEMINI, LLMProvider.CLAUDE],
            "fast": [LLMProvider.DEEPSEEK, LLMProvider.OPENAI],
            "coding": [LLMProvider.DEEPSEEK, LLMProvider.GEMINI, LLMProvider.CLAUDE],
            "reasoning": [LLMProvider.GEMINI, LLMProvider.CLAUDE],
        }
        
        # Score providers
        scores = {}
        for cap in capabilities or ["coding"]:
            for provider in capability_map.get(cap, []):
                scores[provider] = scores.get(provider, 0) + 1
        
        # Sort by score and health
        return sorted(
            scores.keys(),
            key=lambda p: (scores[p], self.health_status.get(p, LLMHealth(p, True, 0)).latency_ms),
            reverse=True
        )
```

---

## 5. Implementation Priority

| Priority | Integration | Effort | Impact |
|----------|-------------|--------|--------|
| 1 | Gemini Pro Fallback | Low | High - Reliability boost |
| 2 | OpenHands Runtime | Medium | High - Better debugging |
| 3 | Perplexity Research | Low | Medium - Better architecture |
| 4 | Codeium/Cursor | Low | Medium - Dev productivity |
| 5 | HuggingFace API | Medium | Low - Specialized tasks |

---

## Quick Start: Adding Gemini Today

1. Get API key: https://makersuite.google.com/app/apikey
2. Add to `.env`: `GEMINI_API_KEY=your_key`
3. Modify `autonomous_pipeline.py` to use `LLMRouter`
4. Complex tasks now route to Gemini automatically

## Quick Start: Adding OpenHands

1. `git clone https://github.com/All-Hands-AI/OpenHands.git ~/openhands`
2. Follow setup in integration guide above
3. Run `start_openhands.sh` on Inference node
4. Enable in pipeline: `ENABLE_OPENHANDS=true`
