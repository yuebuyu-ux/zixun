import json
import os
import asyncio
from jinja2 import Template
from playwright.async_api import async_playwright

async def main():
    # 1. Load Data
    data_path = 'data.json'
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found.")
        return

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 2. Load Template
    template_path = 'template.html'
    if not os.path.exists(template_path):
        print(f"Error: {template_path} not found.")
        return

    with open(template_path, 'r', encoding='utf-8') as f:
        template_str = f.read()

    # 3. Render HTML
    template = Template(template_str)
    html_content = template.render(**data)

    # Save rendered HTML for debugging/viewing
    output_html_path = 'output.html'
    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"HTML generated at {os.path.abspath(output_html_path)}")

    # 4. Generate Image using Playwright
    print("Starting Playwright to generate image...")
    try:
        async with async_playwright() as p:
            # Launch browser (chromium)
            # Try to launch. If it fails due to missing executable, we might need to install it.
            browser = await p.chromium.launch()
            page = await browser.new_page(device_scale_factor=2) # Higher scale factor for retina-like quality
            
            # Load the HTML file
            # Playwright works best with file:// URLs for local files
            file_url = f"file:///{os.path.abspath(output_html_path).replace(os.sep, '/')}"
            print(f"Loading {file_url}...")
            await page.goto(file_url)
            
            # Wait for any potential layout shifts or fonts
            await page.wait_for_timeout(500) 
            
            # Get the container element to screenshot exactly that area
            element = await page.query_selector('.container')
            
            if element:
                await element.screenshot(path='result.png')
                print(f"Image generated successfully at {os.path.abspath('result.png')}")
            else:
                print("Error: Could not find .container element in the page.")
                
            await browser.close()
    except Exception as e:
        print(f"Error during Playwright execution: {e}")
        print("Tip: You might need to run 'playwright install' if this is the first time.")

if __name__ == "__main__":
    asyncio.run(main())
