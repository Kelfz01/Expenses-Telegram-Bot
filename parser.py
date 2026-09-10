import os
import time
from typing import Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError

class SlipData(BaseModel):
    is_valid_slip: bool = Field(description="True if this is a genuine bank transfer receipt, invoice, or payment slip")
    trans_type: str = Field(description="'expense' (transfer out/paid) or 'income' (transfer in/received)")
    amount: float = Field(description="Total transferred amount as numeric float")
    currency: Optional[str] = Field(description="Currency code like THB, USD, EUR, etc. Default THB if in Thailand")
    category: str = Field(description="Normalized category inferred from Note/Memo/Remark or merchant name (e.g., Food & Dining, Groceries, Transport, Utilities, Shopping, Bills, Entertainment, Transfer)")
    raw_note: Optional[str] = Field(description="Exact text from 'Note', 'Memo', 'Remark', 'Description' on the slip")
    sender: Optional[str] = Field(description="Sender name or account snippet")
    receiver: Optional[str] = Field(description="Receiver or merchant name")
    bank: Optional[str] = Field(description="Bank or payment service name (e.g. KBank, SCB, Revolut, Chase, Bangkok Bank, PromptPay)")
    trans_datetime: str = Field(description="Transaction date and time strictly formatted as 'YYYY-MM-DD HH:MM:SS'. If year is missing, assume current year.")

CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]

def parse_slip_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[SlipData]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set.")
    
    client = genai.Client(api_key=api_key)

    prompt = """
You are an expert financial receipt & mobile banking slip analyzer.
Analyze this slip image (in English language) and extract structured transaction data:

1. Amount: Find the transfer amount. Must be numeric without commas.
2. Note/Memo/Remark: Check for fields like 'Note', 'Memo', 'Remark', 'Description', or 'Message'.
   - Extract the exact note into `raw_note`.
   - Use this note primarily to assign an accurate `category` (e.g. if note is 'lunch with team', category is 'Food & Dining').
   - If no note is present, infer category from the merchant / recipient name. If completely unknown, use 'General'.
3. Date & Time:
   - Convert to standard 24-hour format: 'YYYY-MM-DD HH:MM:SS'.
4. Transaction Type:
   - 'expense' if money was sent/paid.
   - 'income' if money was received.
"""

    last_error = None
    for model_name in CANDIDATE_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(
                            data=image_bytes,
                            mime_type=mime_type,
                        ),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SlipData,
                        temperature=0.1
                    )
                )

                if response.parsed:
                    return response.parsed
            except APIError as e:
                last_error = e
                if e.code in (429, 503):
                    time.sleep(1.5)
                    continue
                raise e
            except Exception as e:
                last_error = e
                break

    if last_error:
        raise last_error
    return None
