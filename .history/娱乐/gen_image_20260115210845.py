import json
import os
import asyncio
from jinja2 import Template
from playwright.async_api import async_playwright

async def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 1. Load Data
    data_path = os.path.join(base_dir, 'data.json')
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found.")
        return

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 2. Load Template
    template_path = None
    template_candidates = [
        os.path.join(base_dir, 'template_ent.html'),
        os.path.join(base_dir, 'template.html'),
    ]
    for candidate in template_candidates:
        if os.path.exists(candidate):
            template_path = candidate
            break
    if not template_path:
        print(f"Error: template file not found in {base_dir}.")
        return

    with open(template_path, 'r', encoding='utf-8') as f:
        template_str = f.read()

    # 3. Render HTML
    template = Template(template_str)
    html_content = template.render(**data)

    # Save rendered HTML for debugging/viewing
    output_html_path = os.path.join(base_dir, 'output.html')
    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"HTML generated at {os.path.abspath(output_html_path)}")

    # 4. Generate Image using Playwright
    print("Starting Playwright to generate image...")
    
    # Construct output filename based on date
    date_str = data.get('date_info', {}).get('date_str', 'unknown_date')
    # Clean up filename just in case (though user provided format seems safe)
    safe_filename = "".join([c for c in date_str if c.isalnum() or c in (' ', '.', '_', '年', '月', '日')]).strip()
    output_dir = os.path.join(base_dir, '每日一图')
    os.makedirs(output_dir, exist_ok=True)
    output_image_path = os.path.join(output_dir, f'{safe_filename}.png')
    
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
                await element.screenshot(path=output_image_path)
                print(f"Image generated successfully at {os.path.abspath(output_image_path)}")
            else:
                print("Error: Could not find .container element in the page.")
                
            await browser.close()
    except Exception as e:
        print(f"Error during Playwright execution: {e}")
        print("Tip: You might need to run 'playwright install' if this is the first time.")

if __name__ == "__main__":
    asyncio.run(main())
