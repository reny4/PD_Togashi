from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # .env から OPENAI_API_KEY を読み込む
client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o-mini",  
    messages=[
    {"role": "system", "content": "あなたは親切なAIアシスタントです。"},
    {"role": "user", "content": "Pythonとは何ですか？"}
]
)

print(response.choices[0].message.content)