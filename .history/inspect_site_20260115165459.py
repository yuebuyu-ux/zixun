import asyncio
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        print("Navigating to https://tophub.today/daily ...")
        await page.goto("https://tophub.today/daily")
        await page.wait_for_timeout(2000)
        
        # Try to find news items
        # Based on common structures, looking for lists or articles
        # Let's grab the text content of the main area or specific classes if we can guess
        
        # Get all text to see what we have
        content = await page.content()
        
        # Extract some potential titles using selectors
        # Common selectors for TopHub might be .item-title, .title, etc.
        # Let's try to get elements that look like news items
        
        # Strategy: Get full text content
        # Also try to dump the HTML to a file so we can inspect it manually (simulated)
        content = await page.content()
        with open('debug_page.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Page content saved to debug_page.html")
        
        # Try to extract text from common containers
        # Look for divs with class containing 'item' or 'news' or 'card'
        # Or just dump the first 5000 chars of body text
        body_text = await page.inner_text('body')
        print("First 2000 chars of body text:")
        print(body_text[:2000])
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
