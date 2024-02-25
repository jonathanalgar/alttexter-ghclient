import asyncio
import logging
import os
import subprocess
import time

from github import Github


class GitHubHandler:
    """
    Manages interactions with GitHub using PyGithub, focusing on pull request operations.

    Attributes:
        github_obj (Github): PyGithub instance for GitHub API interactions.
        repo (Repository): GitHub repository associated with the pull request.
        pr (PullRequest): Pull request object for commenting or reviewing.
    """
    def __init__(self, repo_name, pr_number, silent_mode=False):
        """
        Initializes GitHubHandler with repository and pull request details.

        Args:
            repo_name (str): Repository name.
            pr_number (int): Pull request number.
        """
        token_var_name = os.getenv('ALTTEXTER_GITHUB_TOKEN_OVERRIDE')
        if token_var_name:
            github_token = os.getenv(token_var_name)
            if github_token:
                logging.info(f"Using custom token provided by the environment variable: {token_var_name}")
            else:
                logging.error(f"The environment variable {token_var_name} does not exist or is not set. Falling back to GITHUB_TOKEN.")
                github_token = os.getenv('GITHUB_TOKEN')
        else:
            logging.debug("No custom token override provided; using GITHUB_TOKEN.")
            github_token = os.getenv('GITHUB_TOKEN')

        self.github_obj = Github(github_token)
        self.repo = self.github_obj.get_repo(repo_name)
        self.pr = self.repo.get_pull(pr_number)

        self.silent_mode = silent_mode
        if self.silent_mode:
            logging.info("Running in silent mode. Comments to the PR will be minimized.")

    def post_comment(self, message):
        """
        Posts a comment to the pull request.

        Args:
            message (str): Comment content.
        """
        if self.silent_mode:
            logging.info(f"Silent mode enabled. Intended comment: {message}")
            return

        self.pr.create_issue_comment(message)

    def post_generic_review_comment(self, file_path, review_message):
        """
        Posts a review comment on a specific file in the pull request.

        Args:
            file_path (str): Relative file path in the repository.
            review_message (str): Review comment content.
        """
        try:
            pr = self.repo.get_pull(self.pr.number)
            commit_obj = self.repo.get_commit(pr.head.sha)

            self.pr.create_review_comment(
                body=review_message,
                commit=commit_obj,
                path=file_path,
                subject_type='file'
            )
        except Exception as e:
            logging.error(f'Failed to post review comment for {file_path}: {str(e)}')

    def commit_and_push(self, updated_files, commit_message):
        """
        Commits and pushes specified files to the git repository.

        Args:
            updated_files (list of str): File paths to commit.
            commit_message (str): Commit message.

        Returns:
            bool: True if successful, False otherwise.
        """
        file_paths_str = "[" + ", ".join(updated_files) + "]"
        logging.info(f"{file_paths_str} Initiating commit and push process")

        git_username = os.getenv('ALTTEXTER_GITHUB_USERNAME') or 'github-actions'
        git_email = os.getenv('ALTTEXTER_GITHUB_EMAIL') or 'github-actions@github.com'

        if os.getenv('ALTTEXTER_GITHUB_USERNAME'):
            logging.info(f"Using custom Git username: {git_username}")
        else:
            logging.info("Using default Git username: 'github-actions'")

        if os.getenv('ALTTEXTER_GITHUB_EMAIL'):
            logging.info(f"Using custom Git email: {git_email}")
        else:
            logging.info("Using default Git email: 'github-actions@github.com'")

        try:
            # Configure Git to allow operations in the current directory
            current_directory = os.getcwd()
            subprocess.run(['git', 'config', '--global', '--add', 'safe.directory', current_directory], check=True)
            subprocess.run(['git', 'config', '--global', 'user.name', git_username], check=True)
            subprocess.run(['git', 'config', '--global', 'user.email', git_email], check=True)

            if subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout:
                subprocess.run(['git', 'add'] + updated_files, check=True)
                commit_result = subprocess.run(['git', 'commit', '-m', commit_message], capture_output=True, text=True)

                if commit_result.returncode == 0:
                    logging.info(f"{file_paths_str} Changes committed successfully")
                    push_result = subprocess.run(['git', 'push'], capture_output=True, text=True)
                    if push_result.returncode == 0:
                        logging.info(f"{file_paths_str} Changes pushed to remote repository successfully.")
                        return True
                    else:
                        logging.error(f"{file_paths_str} Failed to push changes")
                        return False
                else:
                    logging.error(f"{file_paths_str} Failed to commit changes")
                    return False
            else:
                logging.info(f"{file_paths_str} No changes to commit")
                return False
        except subprocess.CalledProcessError as e:
            logging.error(f"{file_paths_str} Error during git operations: {e}")
            return False

    def get_file_status(self, file_path):
        """
        Retrieves the status of a file in the context of the pull request.

        The method checks the list of files in the pull request and returns the status
        of the specified file. The status can indicate whether the file is added, modified, or deleted.

        Args:
            file_path (str): The relative path of the file in the repository for which the status is required.
        """
        pr_files = self.pr.get_files()
        return next(
            (file.status for file in pr_files if file.filename == file_path), None
        )


class RateLimiter:
    """
    Token bucket algorithm-based rate limiter for API requests.

    Attributes:
        rate (int): Maximum requests per time frame.
        per (float): Time frame for rate limit in seconds.
        allowance (float): Current available tokens.
        last_check (float): Last timestamp of rate limit check.
    """
    def __init__(self, rate, per):
        """
        Initializes RateLimiter with a specific rate and time period.

        Args:
            rate (int): Allowed calls per time period.
            per (float): Time period in seconds.
        """
        self.rate = rate
        self.per = per
        self.allowance = rate
        self.last_check = time.monotonic()

    async def wait_for_token(self, file_path):
        """
        Waits for token availability based on the rate limit before proceeding with a request.
        """
        while self.allowance < 1:
            logging.info(f"[{file_path}] Rate limit reached. Waiting for token availability...")
            await asyncio.sleep(5)
            current_time = time.monotonic()
            time_passed = current_time - self.last_check
            self.last_check = current_time
            self.allowance += time_passed * (self.rate / self.per)
            self.allowance = min(self.allowance, self.rate)
        logging.info(f"[{file_path}] Token available. Proceeding with request.")
        self.allowance -= 1