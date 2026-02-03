import json
import os
import asyncio
from jinja2 import Template
from playwright.async_api import async_playwright

async def generate_image_from_file(data_path, template_path, output_image_path):
    """
    通用图片生成函数
    """
    # 1. Load Data
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found.")
        return

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 2. Load Template
    if not os.path.exists(template_path):
        print(f"Error: {template_path} not found.")
        return

    with open(template_path, 'r', encoding='utf-8') as f:
        template_str = f.read()

    # 3. Render HTML
    template = Template(template_str)
    html_content = template.render(**data)

    # Save rendered HTML for debugging/viewing (use a unique name if possible, or just overwrite)
    # To avoid conflict, use output_image_path's name
    base_name = os.path.splitext(os.path.basename(output_image_path))[0]
    output_html_path = f'output_{base_name}.html'
    
    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"HTML generated at {os.path.abspath(output_html_path)}")

    # 4. Generate Image using Playwright
    print("Starting Playwright to generate image...")
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_image_path), exist_ok=True)
    
    try:
        async with async_playwright() as p:
            # Launch browser (chromium)
            browser = await p.chromium.launch()
            page = await browser.new_page(device_scale_factor=2) # Higher scale factor for retina-like quality
            
            # Load the HTML file
            file_url = f"file:///{os.path.abspath(output_html_path).replace(os.sep, '/')}"
            print(f"Loading {file_url}...")
            await page.goto(file_url)
            
            # Wait for any potential layout shifts or fonts
            await page.wait_for_timeout(500) 
            
            # Get the container element to screenshot exactly that area
            element = await page.query_selector('.container')
            
            if element:
                await element.screenshot(path=output_image_path)
                print(f"Image generated successfully at {os.path.abspath(output_image_path)}")
            else:
                print("Error: Could not find .container element in the page.")
                
            await browser.close()
    except Exception as e:
        print(f"Error during Playwright execution: {e}")
        print("Tip: You might need to run 'playwright install' if this is the first time.")

async def main():
    # Default behavior for auto_process.py
    data_path = 'data.json'
    template_path = 'template.html'
    
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        date_str = data.get('date_info', {}).get('date_str', 'unknown_date')
        safe_filename = "".join([c for c in date_str if c.isalnum() or c in (' ', '.', '_', '年', '月', '日')]).strip()
        output_image_path = os.path.join('每日一图', f'{safe_filename}.png')
        
        await generate_image_from_file(data_path, template_path, output_image_path)
    else:
        print("data.json not found, cannot run default process.")

if __name__ == "__main__":
    asyncio.run(main())
