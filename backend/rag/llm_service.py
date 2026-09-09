import os
import re
from typing import Optional, Dict, Any, List
from backend.config import (
    GEMINI_API_KEY,
    GROQ_API_KEY,
    OPENAI_API_KEY,
    LLM_PROVIDER
)

class LLMService:
    def __init__(self):
        self.provider = LLM_PROVIDER
        self.client = None
        self._init_client()

    def _init_client(self):
        # Auto-detect available API keys
        if (self.provider == "gemini" or self.provider == "auto") and (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY):
            try:
                from google import genai
                api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
                self.client = genai.Client(api_key=api_key)
                self.provider = "gemini"
                print("LLM Service initialized with Google Gemini API.")
                return
            except Exception as e:
                print(f"Failed to init Gemini client: {e}")

        if (self.provider == "groq" or self.provider == "auto") and (os.getenv("GROQ_API_KEY") or GROQ_API_KEY):
            try:
                from groq import Groq
                api_key = os.getenv("GROQ_API_KEY") or GROQ_API_KEY
                self.client = Groq(api_key=api_key)
                self.provider = "groq"
                print("LLM Service initialized with Groq API.")
                return
            except Exception as e:
                print(f"Failed to init Groq client: {e}")

        if (self.provider == "openai" or self.provider == "auto") and (os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY):
            try:
                from openai import OpenAI
                api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
                self.client = OpenAI(api_key=api_key)
                self.provider = "openai"
                print("LLM Service initialized with OpenAI API.")
                return
            except Exception as e:
                print(f"Failed to init OpenAI client: {e}")

        self.provider = "mock"
        print("No active LLM cloud key found. Using Mock / Rule-based synthesis for offline safety.")

    def set_api_key(self, provider: str, api_key: str) -> Dict[str, Any]:
        """Dynamically configures and activates an LLM API key."""
        clean_key = api_key.strip()
        provider = provider.lower()
        
        env_map = {
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY"
        }
        
        env_var = env_map.get(provider)
        if not env_var:
            raise ValueError(f"Unsupported provider: {provider}")
        
        # Update in-process environment variable
        os.environ[env_var] = clean_key
        
        # Update .env file on disk
        from backend.config import BASE_DIR
        env_path = BASE_DIR / ".env"
        try:
            if not env_path.exists():
                env_path.touch()
            content = env_path.read_text(encoding="utf-8")
            pattern = rf"^{re.escape(env_var)}=.*$"
            new_line = f"{env_var}={clean_key}"
            if re.search(pattern, content, flags=re.MULTILINE):
                updated_content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
            else:
                updated_content = content.rstrip() + f"\n{new_line}\n"
            env_path.write_text(updated_content, encoding="utf-8")
        except Exception as e:
            print(f"Notice: Failed writing to .env file: {e}")
            
        # Re-initialize client
        self.provider = provider if clean_key else "auto"
        self._init_client()
        
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Returns the current active LLM provider and available keys status."""
        gemini_k = os.getenv("GEMINI_API_KEY", "")
        groq_k = os.getenv("GROQ_API_KEY", "")
        openai_k = os.getenv("OPENAI_API_KEY", "")
        
        def mask_key(k: str) -> Optional[str]:
            if not k:
                return None
            if len(k) <= 8:
                return "••••••••"
            return f"{k[:4]}••••{k[-4:]}"

        return {
            "active_provider": self.provider,
            "has_gemini": bool(gemini_k),
            "has_groq": bool(groq_k),
            "has_openai": bool(openai_k),
            "gemini_masked": mask_key(gemini_k),
            "groq_masked": mask_key(groq_k),
            "openai_masked": mask_key(openai_k),
            "mode": "cloud_llm" if self.provider in ["gemini", "groq", "openai"] and self.client else "offline_extraction"
        }

    def generate(self, prompt: str, system_instruction: Optional[str] = None, temperature: float = 0.2) -> str:
        """Dispatches generation to active provider with error handling and fallbacks."""
        # Re-check environment variables in case user added them dynamically
        if self.provider == "mock":
            self._init_client()

        # 1. Try Gemini if configured
        if (self.provider in ["gemini", "auto"]) and (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY):
            if not self.client or self.provider != "gemini":
                try:
                    from google import genai
                    self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY)
                except Exception as e:
                    print(f"Gemini client init error: {e}")
            if self.client is not None:
                gemini_models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-2.5-pro']
                for model_name in gemini_models:
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config={
                                "system_instruction": system_instruction,
                                "temperature": temperature
                            } if system_instruction else {"temperature": temperature}
                        )
                        if response and response.text:
                            return response.text.strip()
                    except Exception as e:
                        print(f"Gemini API ({model_name}) error: {e}")

        # 2. Try Groq if configured
        if (self.provider in ["groq", "auto"]) and (os.getenv("GROQ_API_KEY") or GROQ_API_KEY):
            try:
                from groq import Groq
                groq_client = Groq(api_key=os.getenv("GROQ_API_KEY") or GROQ_API_KEY)
                groq_models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "groq/compound-mini", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"]
                messages = []
                if system_instruction:
                    messages.append({"role": "system", "content": system_instruction})
                messages.append({"role": "user", "content": prompt})
                for g_model in groq_models:
                    try:
                        chat_completion = groq_client.chat.completions.create(
                            messages=messages,
                            model=g_model,
                            temperature=temperature,
                        )
                        if chat_completion and chat_completion.choices:
                            content = chat_completion.choices[0].message.content
                            if content:
                                return content.strip()
                    except Exception as e:
                        print(f"Groq API ({g_model}) error: {e}")
            except Exception as e:
                print(f"Groq client init error: {e}")

        # 3. Try OpenAI if configured
        if (self.provider in ["openai", "auto"]) and (os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY):
            try:
                from openai import OpenAI
                openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY)
                messages = []
                if system_instruction:
                    messages.append({"role": "system", "content": system_instruction})
                messages.append({"role": "user", "content": prompt})
                response = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=temperature
                )
                if response and response.choices:
                    content = response.choices[0].message.content
                    if content:
                        return content.strip()
            except Exception as e:
                print(f"OpenAI API error: {e}")

        # Intelligent local fallback when no API key is set or cloud calls fail
        return self._local_fallback_answer(prompt)

    def _local_fallback_answer(self, prompt: str) -> str:
        """Provides an extractive, coherent synthesis from context if LLM cloud key is absent."""
        # 1. SQL Query Generation
        if "SQLite database 'mediassist.db'" in prompt or "translate the user's natural language question into a single, valid, optimized SQL query" in prompt:
            # Extract only the user's question to avoid matching table column names in the schema description
            user_q_match = re.search(r"User Question:\s*(.*?)(?:\n\s*SQL Query:|\Z)", prompt, re.DOTALL | re.IGNORECASE)
            lower_p = user_q_match.group(1).lower().strip() if user_q_match else prompt.lower()

            if "claim" in lower_p:
                if "escalat" in lower_p:
                    return "SELECT COUNT(*) as escalated_claims FROM claims WHERE status = 'escalated';"
                elif "approved amount" in lower_p or ("approved" in lower_p and ("sum" in lower_p or "total" in lower_p)):
                    if "cardiology" in lower_p:
                        return "SELECT SUM(approved_amount) as total_approved FROM claims WHERE status = 'approved' AND LOWER(department) = 'cardiology';"
                    elif "nephrology" in lower_p:
                        return "SELECT SUM(approved_amount) as total_approved FROM claims WHERE status = 'approved' AND LOWER(department) = 'nephrology';"
                    return "SELECT SUM(approved_amount) as total_approved FROM claims WHERE status = 'approved';"
                elif "claimed amount" in lower_p or "total amount" in lower_p:
                    return "SELECT SUM(claimed_amount) as total_claimed FROM claims;"
                elif "status" in lower_p or "breakdown" in lower_p:
                    return "SELECT status, COUNT(*) as count, SUM(claimed_amount) as total_claimed, SUM(approved_amount) as total_approved FROM claims GROUP BY status;"
                elif "insurer" in lower_p:
                    return "SELECT insurer, COUNT(*) as claim_count, SUM(claimed_amount) as total_claimed FROM claims GROUP BY insurer ORDER BY total_claimed DESC;"
                elif "department" in lower_p:
                    return "SELECT department, COUNT(*) as count, SUM(approved_amount) as total_approved FROM claims GROUP BY department ORDER BY total_approved DESC;"
                elif "pending" in lower_p:
                    return "SELECT COUNT(*) as pending_claims FROM claims WHERE status = 'pending';"
                elif "reject" in lower_p:
                    return "SELECT COUNT(*) as rejected_claims FROM claims WHERE status = 'rejected';"
                elif "average" in lower_p or "avg" in lower_p:
                    return "SELECT AVG(claimed_amount) as avg_claimed, AVG(approved_amount) as avg_approved FROM claims;"
                return "SELECT COUNT(*) as total_claims FROM claims;"
            elif "equipment" in lower_p or "ticket" in lower_p or "maintenance" in lower_p or "category" in lower_p:
                if "category" in lower_p and ("most" in lower_p or "open" in lower_p or "highest" in lower_p):
                    return "SELECT category, COUNT(*) as open_tickets FROM maintenance_tickets WHERE status != 'resolved' GROUP BY category ORDER BY open_tickets DESC LIMIT 1;"
                elif "status" in lower_p or "breakdown" in lower_p:
                    return "SELECT status, COUNT(*) as count FROM maintenance_tickets GROUP BY status;"
                elif "campus" in lower_p:
                    return "SELECT campus, COUNT(*) as count FROM maintenance_tickets GROUP BY campus ORDER BY count DESC;"
                elif "open" in lower_p or "in progress" in lower_p or "pending" in lower_p:
                    return "SELECT category, COUNT(*) as open_tickets FROM maintenance_tickets WHERE status != 'resolved' GROUP BY category ORDER BY open_tickets DESC;"
                return "SELECT category, equipment_name, status, issue_type FROM maintenance_tickets LIMIT 10;"
            return "SELECT COUNT(*) as total_records FROM claims;"

        # 2. SQL Result Synthesis
        if "The database returned the following result records (JSON):" in prompt or "result records (JSON):" in prompt or "SQL Query Executed:" in prompt:
            match_data = re.search(r"result records \(JSON\):\s*(.*?)(?:\n\nProvide|\Z)", prompt, re.DOTALL | re.IGNORECASE)
            data_str = match_data.group(1).strip() if match_data else ""
            
            try:
                import ast
                records = ast.literal_eval(data_str) if data_str else []
            except Exception:
                records = None

            if isinstance(records, list) and len(records) > 0:
                if len(records) == 1 and len(records[0]) <= 2:
                    row = records[0]
                    items = []
                    for k, v in row.items():
                        clean_k = k.replace("_", " ").title()
                        if isinstance(v, (int, float)) and ("amount" in k.lower() or "total" in k.lower() or "sum" in k.lower()):
                            items.append(f"**{clean_k}:** ₹{v:,.2f}")
                        else:
                            items.append(f"**{clean_k}:** {v}")
                    return "### Analytical Summary\n\nBased on MediAssist database records:\n- " + " | ".join(items)
                
                # Multi-row table
                headers = list(records[0].keys())
                header_line = "| " + " | ".join(h.replace("_", " ").title() for h in headers) + " |"
                sep_line = "| " + " | ".join("---" for _ in headers) + " |"
                data_lines = []
                for r in records[:15]:
                    row_vals = []
                    for h in headers:
                        val = r.get(h)
                        if isinstance(val, (int, float)) and ("amount" in h.lower() or "sum" in h.lower()):
                            row_vals.append(f"₹{val:,.2f}")
                        else:
                            row_vals.append(str(val) if val is not None else "—")
                    data_lines.append("| " + " | ".join(row_vals) + " |")
                table_md = "\n".join([header_line, sep_line] + data_lines)
                return f"### Analytical Summary\n\nBased on MediAssist database records:\n\n{table_md}"
            elif isinstance(records, list) and len(records) == 0:
                return "The query executed successfully, but no matching records were found in the database."
            return f"Based on the relational database records: {data_str}"

        # 3. Document RAG: extract key context lines cleanly
        context_match = re.search(r"Context:\s*(.*?)\s*\n\nQuestion:", prompt, re.DOTALL) or re.search(r"Documentation Context:\s*(.*?)\s*\n\nUser Question:", prompt, re.DOTALL)
        if context_match:
            context = context_match.group(1).strip()
            clean_paragraphs = []
            for block in context.split("---"):
                lines = [l.strip() for l in block.split("\n") if l.strip() and not l.startswith("Document:") and not l.startswith("| ---")]
                if lines:
                    clean_paragraphs.append("\n".join(lines[:4]))
            if clean_paragraphs:
                return "### Summary from Hospital Records\n\n" + "\n\n".join(f"• {p}" for p in clean_paragraphs[:3])
            return "Based on the retrieved hospital records, the relevant protocols have been cited in the source references below."
        return "MediBot retrieved the relevant hospital documentation according to your access level."

_llm_instance = None

def get_llm_service() -> LLMService:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMService()
    return _llm_instance
