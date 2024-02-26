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
    IMAGE_PATTERN = re.compile(r'!\[(.*?)\]\((.*?)\s*(?:\"(.*?)\"|\'(.*?)\'|\))?\)')

    async def check_url_content_type(self, url, file_path, session):
        """
        Asynchronously checks the content type of the specified URL to determine if it points to a supported image type.

        Args:
            url (str): The URL of the image to check.
            file_path (str): Path of the file containing the URL, used for logging purposes.
            session (aiohttp.ClientSession): The session object used for making HTTP requests.

        Returns:
            bool: True if the content type of the URL is a supported image type; False otherwise.
        """
        headers = {'User-Agent': 'Mozilla/5.0'}

        # Limiting download size to quickly verify if the URL points to a supported image type
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
            # async request to get the content type of the URL as last resort
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    await response.content.read(max_content_length)
                    return any(content_type.startswith(mime) for mime in supported_mime_types)
                return False
        except Exception as e:
            logging.error(f"[{file_path}] Error checking content type for URL {url}: {e}")
            return False

    async def extract_image_paths(self, markdown_content, file_path, session):
        """
        Extracts and categorizes image paths from markdown content into local images and image URLs.

        Args:
            markdown_content (str): The markdown content to be scanned for images.
            file_path (str): The path of the markdown file being processed, used for logging.
            session (aiohttp.ClientSession): The session object used for making HTTP requests to check URL content types.

        Returns:
            tuple: A tuple containing two lists - the first list contains tuples of local image paths and their metadata,
                   and the second list contains tuples of image URLs and their metadata.
        """

        images = self.IMAGE_PATTERN.findall(markdown_content)
        local_images = []
        image_urls = []

        # Parsing the image details: alt text, path, and optional title.
        for image_info in images:
            logging.debug(f"[{file_path}] Regex match: {image_info}")

            alt_text, image_path = image_info[:2]

            title_double, title_single = image_info[2:4]
            title = title_double or title_single

            clean_path, _, _ = image_path.partition('?')
            _, ext = os.path.splitext(clean_path)

            if not title:
                title = None

            # Local image files
            if not clean_path.startswith(('http://', 'https://')):
                if ext.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    local_images.append((alt_text, clean_path, title))
                else:
                    logging.info(f"[{file_path}] Unsupported local image type: {image_path}")
                continue

            # URLs
            if (
                ext
                and ext.lower() in SUPPORTED_IMAGE_EXTENSIONS
                or not ext
                and await self.check_url_content_type(clean_path, file_path, session)
            ):
                image_urls.append((alt_text, image_path, title))
            elif ext and ext.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                logging.info(f"[{file_path}] Unsupported image URL extension: {clean_path}")
            else:
                logging.info(f"[{file_path}] URL does not point to a supported image type: {clean_path}")

        logging.info(f"[{file_path}] Total local images found: {len(local_images)}, Total image URLs found: {len(image_urls)}")
        return local_images, image_urls

    def encode_images(self, images, base_dir, file_path, repo_root):
        """
        Encodes local images specified in the images list to base64 strings.

        Args:
            images (list): A list of tuples representing local images to be encoded. Each tuple contains the image's alt text, path, and optional title.
            base_dir (str): The base directory of the markdown file being processed.
            file_path (str): The path of the markdown file, used for logging.
            repo_root (str): The root directory of the repository containing the markdown file.

        Returns:
            dict: A dictionary mapping the image paths to their base64 encoded strings.
        """
        encoded_images = {}

        # Encoding local images to base64 strings, handling both direct and relative paths.
        for _, image_path, _ in images:
            if image_path.startswith('/'):
                full_image_path = os.path.join(repo_root, image_path.lstrip('/'))
            else:
                full_image_path = os.path.join(base_dir, image_path)

            logging.debug(f"[{file_path}] Calculated full image path: {full_image_path}")

            if not os.path.exists(full_image_path):
                logging.debug(f"[{file_path}] Direct path not found, searching in repository: {image_path}")
                search_path = os.path.join(repo_root, '**', os.path.basename(image_path))
                if search_results := glob.glob(search_path, recursive=True):
                    full_image_path = search_results[0]  # Take the first match
                    logging.debug(f"[{file_path}] Found image at: {full_image_path}")
                else:
                    logging.info(f"[{file_path}] Image not found in repository: {image_path}")
                    continue

            if image_path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                encoded_images[image_path] = self.encode_image(full_image_path)
            else:
                logging.info(f"[{file_path}] Unsupported image type: {image_path} (Full path: {full_image_path})")
        return encoded_images

    def encode_image(self, image_path):
        """
        Encodes a single image file located at the specified path to a base64 string.

        Args:
            image_path (str): The filesystem path to the image file.

        Returns:
            str: The base64 encoded string representation of the image file.
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    async def get_image_metadata(self, session, markdown_content, file_path, encoded_images, image_urls, alttexter_endpoint, rate_limiter):
        """
        Asynchronously retrieves image metadata for both local and remote images specified in the markdown content.

        Args:
            session (aiohttp.ClientSession): HTTP session for making requests.
            markdown_content (str): Markdown content from which to extract image metadata.
            file_path (str): Path of the markdown file, used for logging.
            encoded_images (dict): Local images encoded in base64 format.
            image_urls (list): URLs of remote images.
            alttexter_endpoint (str): Endpoint URL for the ALTTEXTER service.
            rate_limiter (RateLimiter): Rate limiter for controlling request frequency.

        Returns:
            dict: A dictionary with a success flag and either the retrieved image metadata or an error message.
        """
        ALTTEXTER_TIMEOUT = 240

        response_structure = {"success": False, "data": None}

        image_urls_only = [url for _, url, _ in image_urls]

        await rate_limiter.wait_for_token(file_path)
        logging.info(f"[{file_path}] Sending request to ALTTEXTER_ENDPOINT")
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
            async with session.post(alttexter_endpoint, json=payload, headers=headers, timeout=ALTTEXTER_TIMEOUT) as response:
                response.raise_for_status()
                response_structure["data"] = await response.json()
                response_structure["success"] = True
        except asyncio.TimeoutError:
            logging.error(f"[{file_path}] Request to ALTTEXTER_ENDPOINT timed out")
        except aiohttp.ClientResponseError as e:
            logging.error(f"[{file_path}] HTTP Response Error: {e}")
        except Exception as e:
            logging.error(f"[{file_path}] An unexpected error occurred: {e}")

        return response_structure

    def update_image_metadata(self, markdown_content, image_metadata, base_dir, repo_root, is_ipynb):
        """
        Updates the markdown content with new alt text and title attributes for images based on the provided metadata.

        Args:
            markdown_content (str): The original markdown content.
            image_metadata (list): A list of metadata for each image.
            base_dir (str): The base directory of the markdown file.
            repo_root (str): The root directory of the repository.
            is_ipynb (bool): Flag indicating if the file is a Jupyter Notebook.

        Returns:
            tuple: The updated markdown content and a list of images that were not updated due to missing metadata.
        """
        images_not_updated = []

        def replacement(match):
            groups = match.groups()
            _, image_path = groups[:2]
            title_double, title_single = groups[2:4] if len(groups) == 4 else ("", "")
            image_title = title_double or title_single or ""

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
                    return f"![{alt_text}]({image_path} '{title}')"
                else:
                    return f'![{alt_text}]({image_path} "{title}")'
            else:
                images_not_updated.append(image_path)
                return match.group(0)

        # Replacing the existing image markdown syntax with updated alt text and title attributes
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
    file_path = file.filename

    logging.info(f"[{file_path}] Starting to process file")

    if not file_path.lower().endswith(SUPPORTED_FILE_EXTENSIONS):
        logging.info(f"[{file_path}] Skipping non-markdown formatted file")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as md_file:
            markdown_content = md_file.read()

        local_images, image_urls = await metadata_updater.extract_image_paths(markdown_content, file_path, session)

        base_dir = os.path.dirname(file_path)
        repo_root = os.getcwd()
        encoded_images = metadata_updater.encode_images(local_images, base_dir, file_path, repo_root)

        is_ipynb = file_path.lower().endswith('.ipynb')

        local_complete_check = all(alt and title for alt, _, title in local_images)
        url_complete_check = all(alt and title for alt, _, title in image_urls)

        if local_complete_check and url_complete_check:
            logging.info(f"[{file_path}] No update needed")
            return

        response = await metadata_updater.get_image_metadata(session, markdown_content, file_path, encoded_images, image_urls, alttexter_endpoint, rate_limiter)

        if not response["success"]:
            logging.error(f"[{file_path}] Failed to get a response from ALTTEXTER_ENDPOINT")
            github_handler.post_comment(f"Failed to get a response from the ALTTEXTER_ENDPOINT for file `{file_path}`. Please check the logs for more details.")
            return

        image_metadata = response["data"].get('images', [])
        updated_content, images_not_updated = metadata_updater.update_image_metadata(
            markdown_content, image_metadata, base_dir, repo_root, is_ipynb
        )

        if updated_content != markdown_content:
            logging.info(f"[{file_path}] Writing updated metadata to file")
            with open(file_path, 'w', encoding='utf-8') as md_file:
                md_file.write(updated_content)

            commit_message = f"Update image alt and title attributes in {file_path}"
            if commit_push_successful := github_handler.commit_and_push([file_path], commit_message):
                review_message = "Please check the LLM generated alt-text and title attributes in this file as they may contain inaccuracies."
                if run_url := response["data"].get('run_url'):
                    review_message += f" [Explore how the LLM generated them.]({run_url})"
                github_handler.post_generic_review_comment(file_path, review_message)
            else:
                logging.error(f"[{file_path}] Failed to commit and push changes")
        elif images_not_updated:
            logging.info(f"[{file_path}] Some images were not updated: {images_not_updated}")
        else:
            logging.info(f"[{file_path}] Metadata is already up to date")

    except Exception as e:
        logging.error(f"[{file_path}] Error processing file: {e}")
        github_handler.post_comment(f"Error processing file `{file_path}`")


async def main():
    """
    Main asynchronous function to run the script.
    """
    rate_limit = int(os.getenv('ALTTEXTER_RATEMINUTE'))
    rate_limiter = RateLimiter(rate=rate_limit, per=60)

    repo_name = os.getenv('GITHUB_REPOSITORY')
    pr_number = int(os.getenv('PR_NUMBER'))
    alttexter_endpoint = os.getenv('ALTTEXTER_ENDPOINT')

    silent_mode = os.getenv('ALTTEXTER_SILENTMODE', '0').lower() in ['true', '1']

    github_handler = GitHubHandler(repo_name, pr_number, silent_mode)
    metadata_updater = ImageMetadataUpdater()

    async with aiohttp.ClientSession() as session:
        files = github_handler.pr.get_files()
        logging.debug(f"Files in PR: {[file.filename for file in files]}")

        tasks = [
            asyncio.create_task(process_file(session, file, alttexter_endpoint, github_handler, metadata_updater, rate_limiter))
            for file in files
            if file.filename.lower().endswith(SUPPORTED_FILE_EXTENSIONS) and github_handler.get_file_status(file.filename) in ['added', 'modified']
        ]

        logging.debug(f"Processing tasks: {tasks}")
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
