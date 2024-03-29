import discord
import os
import openai
from dotenv import load_dotenv
from PIL import Image
import requests
from io import BytesIO

load_dotenv()
CHATGPT_TOKEN = os.getenv('CHATGPT_API_KEY')
DISCORD_TOKEN = os.getenv('DISCORD_BOT_API_KEY')
GEMINI_TOKEN = os.getenv('GOOGLE_AI_API_KEY')
openai.api_key = CHATGPT_TOKEN

intents = discord.Intents.all()
client = discord.Client(intents=intents)

async def query_chatgpt(prompt):
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=1024,
        n=1,
        stop=None,
        temperature=0.5
    )
    return response.choices[0].text

async def get_file(message):
    # Check if the message has an attached image
    if len(message.attachments) == 0:
        await message.channel.send("Please attach an image to edit.")
        return

    # Get the attached image file
    attached_file = message.attachments[0]

    # Download the attached image file
    response = requests.get(attached_file.url)
    file = BytesIO(response.content)
    return file

def resize_image(file):
    desired_size = 1028

    im = Image.open(file)
    old_size = im.size  # old_size[0] is in (width, height) format

    ratio = float(desired_size)/max(old_size)
    new_size = tuple([int(x*ratio) for x in old_size])
    # use thumbnail() or resize() method to resize the input image

    # thumbnail is a in-place operation

    # im.thumbnail(new_size, Image.ANTIALIAS)

    im = im.resize(new_size, Image.Resampling.LANCZOS)

    # create a new image and paste the resized on it
    new_im = Image.new("RGBA", (desired_size, desired_size))
    new_im.paste(im, ((desired_size-new_size[0])//2,
                        (desired_size-new_size[1])//2))

    byte_stream = BytesIO()
    new_im.save(byte_stream, format='PNG')
    byte_array = byte_stream.getvalue()
    return byte_array

async def query_dalle(prompt):
    response = openai.Image.create(
        prompt=prompt,
        n=1,
        size="1024x1024"
    )
    return response['data'][0]['url']

async def query_dalle_edit(prompt,file):
    byte_array = resize_image(file)
    try:
        response = openai.Image.create_edit(
            image=byte_array,
            # mask=open("mask.png", "rb"),
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        print(response['data'][0]['url'])
    except openai.error.OpenAIError as e:
        print(e.http_status)
        print(e.error)
        return e.error['message']

    return response['data'][0]['url']

async def query_dalle_variation(file):
    byte_array = resize_image(file)
    try:
        response = openai.Image.create_variation(
        image=byte_array,
        n=1,
        size="1024x1024"
        )
        print(response['data'][0]['url'])
    except openai.error.OpenAIError as e:
        print(e.http_status)
        print(e.error)
        return e.error['message']
    
    return response['data'][0]['url']

def query_gemini(prompt):
    try:
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_TOKEN}",
            headers={"Content-Type": "application/json"},
            json={
                "contents":[
                    {
                        "parts":[
                            {"text": prompt}
                        ]
                    }
                ],
                "safetySettings": [
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_NONE"
                    },
                ],
                "generationConfig": {
                    "stopSequences": [
                        "Title"
                    ],
                    "temperature": 0.9,
                    "maxOutputTokens": 800#,
                    # "topP": 1,
                    # "topK": 1
                }
            }
        )
        response.raise_for_status()  # Raise exception for non-2xx status codes
        return response
    except requests.exceptions.RequestException as e:
        if isinstance(e, requests.exceptions.ConnectionError):
            error_message = f"Failed to connect to the API. Please check your internet connection\nStatus code: {response.status_code}"
        elif isinstance(e, requests.exceptions.Timeout):
            error_message = f"Request timed out. Please try again later.\nStatus code: {response.status_code}"
        else:
            error_message = f"An unexpected error occurred: {e}\nsafetyRatings: {response.json()['candidates'][0]['safetyRatings']}"
        return error_message

async def format_embed(response):
    embed = discord.Embed(title="The James Roll says...", description=response, color=0x00ff00)
    return embed



############### BOT COMMANDS ###############

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!jhelp'):
        with open("readme.md", "r") as f:
            text = f.read()
        await message.channel.send(text)

    if message.content.startswith('!jpt'):
        prompt = message.content.replace('!jpt', '').strip()
        response = await query_chatgpt(prompt)
        embed = await format_embed(response)
        await message.channel.send(embed=embed)

    if message.content.startswith('!jpti'):
        prompt = message.content.replace('!jpti', '').strip()
        image_url = await query_dalle(prompt)
        await message.channel.send(image_url)

    if message.content.startswith('!jedit'):
        prompt = message.content.replace('!jedit', '').strip()
        file = await get_file(message)
        edited_image = await query_dalle_edit(prompt,file)
        await message.channel.send(edited_image)
    
    if message.content.startswith('!jvari'):
        file = await get_file(message)
        edited_image = await query_dalle_variation(file)
        await message.channel.send(edited_image)

    if message.content.startswith('!jem'):
        prompt = message.content.replace('!jem', '').strip()
        response = query_gemini(prompt)
        # print(response.json())
        text_response = response.json()['candidates'][0]['content']['parts'][0].get('text')
        embed = await format_embed(text_response)
        await message.channel.send(embed=embed)
        ### DEBUG INFO ###
        ## token_count = response.json()['candidates'][0]['tokenCount'] #doesn't exist?
        finish_reason = response.json()['candidates'][0]['finishReason']
        safety_ratings = response.json()['candidates'][0]['safetyRatings']
        await message.channel.send(f"**Debug info**\nFinish Reason:```{finish_reason}```Safety Ratings:```{safety_ratings}```")

client.run(DISCORD_TOKEN)
