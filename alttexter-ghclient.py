import asyncio
import base64
import glob
import logging
import os
import re

import aiohttp

from ghutils import GitHubHandler, RateLimiter

logging.basicConfig(level=logging.DEBUG,
                    format='%(levelname)s [%(asctime)s] %(message)s',
                    datefmt='%d-%m-%Y %H:%M:%S')

SUPPORTED_FILE_EXTENSIONS = ('.md', '.mdx', '.ipynb')
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.gif', '.jpg', '.jpeg', '.webp')


class ImageMetadataUpdater:
    """
    Handles retrieval and updating of image metadata in markdown files.
    """
    def __init__(self):
        pass

    # Pattern to identify markdown image syntax
    IMAGE_PATTERN = re.compile(r'!\[(.*?)\]\((.*?)\s*(?:\"(.*?)\")?\)')

    async def check_url_content_type(self, url, session):
        """
        Makes a GET request to a URL to check its content type but limits the download size.

        Args:
            url (str): The URL to check.
            session (aiohttp.ClientSession): The session for making HTTP requests.

        Returns:
            bool: True if the content type is a supported image, False otherwise.
        """
        headers = {'User-Agent': 'Mozilla/5.0'}
        max_content_length = 1024

        extension_to_mime = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        supported_mime_types = [extension_to_mime[ext] for ext in SUPPORTED_IMAGE_EXTENSIONS if ext in extension_to_mime]

        try:
            # async request to get the content type of the URL
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    await response.content.read(max_content_length)
                    return any(content_type.startswith(mime) for mime in supported_mime_types)
                return False
        except Exception as e:
            logging.error(f"Error checking content type for URL {url}: {e}")
            return False

    async def extract_image_paths(self, markdown_content, session):
        """
        Extracts image paths and details from markdown content.

        Args:
            markdown_content (str): The markdown content.

        Returns:
            tuple: Lists of local images and image URLs with metadata.
        """

        images = self.IMAGE_PATTERN.findall(markdown_content)
        local_images = []
        image_urls = []

        for image_info in images:
            logging.debug(f"Regex match: {image_info}")

            if len(image_info) == 3:
                alt_text, image_path, title = image_info
            elif len(image_info) == 2:
                alt_text, image_path = image_info
                title = ""
            else:
                logging.warning(f"Unexpected match length: {len(image_info)} for match: {image_info}")
                continue

            clean_path, _, _ = image_path.partition('?')
            _, ext = os.path.splitext(clean_path)

            # Local image files
            if not clean_path.startswith(('http://', 'https://')):
                if ext.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    local_images.append((alt_text, clean_path, title))
                else:
                    logging.info(f"Unsupported local image type: {image_path}")
                continue

            # URLs
            if (
                ext
                and ext.lower() in SUPPORTED_IMAGE_EXTENSIONS
                or not ext
                and await self.check_url_content_type(clean_path, session)
            ):
                image_urls.append((alt_text, image_path))
            elif ext and ext.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                logging.info(f"Unsupported image URL extension: {clean_path}")
            else:
                logging.info(f"URL does not point to a supported image type: {clean_path}")

        logging.info(f"Total local images found: {len(local_images)}, Total image URLs found: {len(image_urls)}")
        return local_images, image_urls

    def encode_images(self, images, base_dir, repo_root):
        """
        Encodes local images in base64 format.

        Args:
            images (list): Local image paths.
            base_dir (str): Base directory of markdown file.
            repo_root (str): Root directory of the repository.

        Returns:
            dict: Mapping of image paths to base64 encoded strings.
        """
        encoded_images = {}
        for _, image_path, _ in images:
            if image_path.startswith('/'):
                full_image_path = os.path.join(repo_root, image_path.lstrip('/'))
            else:
                full_image_path = os.path.join(base_dir, image_path)

            logging.debug(f"Calculated full image path: {full_image_path}")

            if not os.path.exists(full_image_path):
                logging.debug(f"Direct path not found, searching in repository: {image_path}")
                search_path = os.path.join(repo_root, '**', os.path.basename(image_path))
                if search_results := glob.glob(search_path, recursive=True):
                    full_image_path = search_results[0]  # Take the first match
                    logging.debug(f"Found image at: {full_image_path}")
                else:
                    logging.info(f"Image not found in repository: {image_path}")
                    continue

            # Encode if it's a supported image type
            if image_path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                encoded_images[image_path] = self.encode_image(full_image_path)
            else:
                logging.info(f"Unsupported image type: {image_path} (Full path: {full_image_path})")
        return encoded_images

    def encode_image(self, image_path):
        """
        Encodes a single image to a base64 string.

        Args:
            image_path (str): Path of the image to encode.

        Returns:
            str: Base64 encoded string of the image.
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    async def get_image_metadata(self, session, markdown_content, encoded_images, image_urls, alttexter_endpoint, rate_limiter):
        """
        Asynchronously retrieves image metadata from ALTTEXTER_ENDPOINT.

        Args:
            session (aiohttp.ClientSession): HTTP session for requests.
            markdown_content (str): Markdown content with images.
            encoded_images (dict): Base64 encoded local images.
            image_urls (list): Image URLs.
            alttexter_endpoint (str): Metadata service URL.
            rate_limiter (RateLimiter): Rate limiter for requests.

        Returns:
            tuple: Indicates success and response data or error info.
        """
        ALTTEXTER_TIMEOUT = 240

        image_urls_only = [url for _, url in image_urls]

        await rate_limiter.wait_for_token()
        logging.info('Sending request to ALTTEXTER_ENDPOINT')
        headers = {
            "Content-Type": "application/json",
            "X-API-Token": os.getenv('ALTTEXTER_TOKEN')
        }
        payload = {
            "text": markdown_content,
            "images": encoded_images,
            "image_urls": image_urls_only
        }
        try:
            response = await session.post(alttexter_endpoint, json=payload, headers=headers, timeout=ALTTEXTER_TIMEOUT)
            response.raise_for_status()
            response_data = await response.json()
            logging.info(f"Full response from ALTTEXTER_ENDPOINT: {response_data}")
            return True, response_data
        except Exception as e:
            logging.error(f"Error occurred: {e}")
            response_text = await response.text()
            logging.error(f"Full server response: {response_text}")
            return False, {"error": str(e)}

    def update_image_metadata(self, markdown_content, image_metadata, base_dir, repo_root, is_ipynb=False):
        """
        Updates markdown content with new alt-text and title attributes.

        Args:
            markdown_content (str): Original markdown content.
            image_metadata (dict): Image metadata including alt-text and titles.
            base_dir (str): Base directory of markdown file.
            repo_root (str): Repository root directory.
            is_ipynb (bool): Flag for Jupyter Notebook files.

        Returns:
            tuple: Updated markdown content and list of images not updated.
        """
        images_not_updated = []

        def replacement(match):
            image_alt, image_path, image_title = match.groups()

            if image_path.startswith(('http://', 'https://')):
                full_path = image_path
            elif image_path.startswith('/'):
                full_path = os.path.join(repo_root, image_path.lstrip('/'))
            else:
                full_path = os.path.join(base_dir, image_path)

            metadata = next((img for img in image_metadata if img['name'] in [full_path, image_path]), None)

            if metadata:
                alt_text = metadata["alt_text"]
                title = metadata.get("title", image_title or "")
                if is_ipynb:
                    return f'![{alt_text}]({image_path})'
                else:
                    return f'![{alt_text}]({image_path} "{title}")'
            else:
                images_not_updated.append(image_path)
                return match.group(0)

        updated_markdown_content = self.IMAGE_PATTERN.sub(replacement, markdown_content)
        return updated_markdown_content, images_not_updated


async def process_file(session, file, alttexter_endpoint, github_handler, metadata_updater, rate_limiter):
    """
    Processes a markdown file to update image metadata.

    Args:
        session (aiohttp.ClientSession): HTTP session.
        file (GitHub ContentFile): File to process.
        alttexter_endpoint (str): Metadata service URL.
        github_handler (GitHubHandler): GitHub API manager.
        metadata_updater (ImageMetadataUpdater): Metadata updater.
        rate_limiter (RateLimiter): API request rate limiter.
    """
    logging.info(f"Starting to process file: {file.filename}")

    if not file.filename.lower().endswith(SUPPORTED_FILE_EXTENSIONS):
        logging.info(f"Skipping non-markdown formatted file: {file.filename}")
        return

    with open(file.filename, 'r', encoding='utf-8') as md_file:
        markdown_content = md_file.read()

    local_images, image_urls = await metadata_updater.extract_image_paths(markdown_content, session)

    base_dir = os.path.dirname(file.filename)
    repo_root = os.getcwd()
    encoded_images = metadata_updater.encode_images(local_images, base_dir, repo_root)

    is_ipynb = file.filename.lower().endswith('.ipynb')

    local_complete_check = all(alt for alt, _, _ in local_images)
    url_complete_check = all(alt for alt, _ in image_urls)

    if not is_ipynb:
        # For .md and .mdx files, check if all images have both alts and titles
        local_complete_check = all(alt and title for alt, _, title in local_images)
    # For .ipynb files, check if all images have alts
    if local_complete_check and url_complete_check:
        logging.info(f"No update needed for {file.filename}")
        return
    success, response_data = await metadata_updater.get_image_metadata(session, markdown_content, encoded_images, image_urls, alttexter_endpoint, rate_limiter)

    if success:
        image_metadata = response_data.get('images', [])
        updated_content, images_not_updated = metadata_updater.update_image_metadata(
            markdown_content, image_metadata, base_dir, repo_root, is_ipynb
        )

        if updated_content != markdown_content:
            logging.info(f"Writing updated metadata to {file.filename}")
            with open(file.filename, 'w', encoding='utf-8') as md_file:
                md_file.write(updated_content)

            commit_message = f"Update image alt and title attributes in {file.filename}"
            if commit_push_successful := github_handler.commit_and_push(
                [file.filename], commit_message
            ):
                review_message = "Please check the LLM generated alt-text and title attributes in this file as they may contain inaccuracies."
                if response_data.get('run_url'):
                    review_message += " [Explore how the LLM generated them.](" + response_data['run_url'] + ")"
                github_handler.post_generic_review_comment(file.filename, review_message)
            else:
                logging.error(f"Failed to commit and push changes for {file.filename}")
        elif images_not_updated:
            logging.info(f"Some images in {file.filename} were not updated: {images_not_updated}")
    else:
        logging.error(f"Failed to fetch image metadata for {file.filename}: {response_data.get('error', 'Unknown error')}")
        if not updated_content:
            github_handler.post_comment(f"Failed to update image metadata for file: `{file.filename}`. Please try again later.")


async def main():
    """
    Main asynchronous function to run the script.
    """
    rate_limit = int(os.getenv('ALTTEXTER_RATEMINUTE'))
    rate_limiter = RateLimiter(rate=rate_limit, per=60)

    repo_name = os.getenv('GITHUB_REPOSITORY')
    pr_number = int(os.getenv('PR_NUMBER'))
    alttexter_endpoint = os.getenv('ALTTEXTER_ENDPOINT')

    github_handler = GitHubHandler(repo_name, pr_number)
    metadata_updater = ImageMetadataUpdater()

    async with aiohttp.ClientSession() as session:
        files = github_handler.pr.get_files()
        logging.debug(f"Files in PR: {[file.filename for file in files]}")

        tasks = [asyncio.create_task(process_file(session, file, alttexter_endpoint, github_handler, metadata_updater, rate_limiter))
                 for file in files if file.filename.lower().endswith(SUPPORTED_FILE_EXTENSIONS)]

        logging.debug(f"Processing tasks: {tasks}")
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
