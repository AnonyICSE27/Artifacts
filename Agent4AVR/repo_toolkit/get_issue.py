#!/usr/bin/env python3
"""
get_issue.py - 从 GitHub issue URL 获取 issue 文本并打印
"""

import sys
import requests
import argparse
from urllib.parse import urlparse


def get_issue_from_url(issue_url):
    """
    从 GitHub issue URL 获取 issue 数据
    
    Args:
        issue_url: GitHub issue URL
        
    Returns:
        dict: issue 数据
    """
    # 解析 URL 获取 owner, repo, issue_number
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip('/').split('/')
    
    if len(path_parts) < 4 or path_parts[2] != 'issues':
        raise ValueError(f"无效的 GitHub issue URL: {issue_url}")
    
    owner = path_parts[0]
    repo = path_parts[1]
    issue_number = path_parts[3]
    
    # GitHub API URL
    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    
    # 发送请求
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'get_issue.py'
    }
    
    response = requests.get(api_url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"获取 issue 失败: {response.status_code} - {response.text}")
    
    return response.json()


def print_issue_details(issue_data):
    """
    打印 issue 详细信息
    
    Args:
        issue_data: GitHub API 返回的 issue 数据
    """
    print("=" * 80)
    print(f"Issue #{issue_data['number']}: {issue_data['title']}")
    print("=" * 80)
    print(f"URL: {issue_data['html_url']}")
    print(f"状态: {issue_data['state']}")
    print(f"创建者: {issue_data['user']['login']}")
    print(f"创建时间: {issue_data['created_at']}")
    print(f"更新时间: {issue_data['updated_at']}")
    
    if issue_data.get('labels'):
        labels = [label['name'] for label in issue_data['labels']]
        print(f"标签: {', '.join(labels)}")
    
    if issue_data.get('assignee'):
        print(f"负责人: {issue_data['assignee']['login']}")
    
    print("\n" + "=" * 80)
    print("Issue 内容:")
    print("=" * 80)
    print(issue_data['body'] if issue_data['body'] else "(无内容)")
    print("=" * 80)
    
    # 如果有评论，也打印出来
    if issue_data.get('comments', 0) > 0:
        print(f"\n有 {issue_data['comments']} 条评论 (使用 --comments 查看)")


def get_comments(issue_url):
    """
    获取 issue 的评论
    
    Args:
        issue_url: GitHub issue URL
        
    Returns:
        list: 评论列表
    """
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip('/').split('/')
    
    owner = path_parts[0]
    repo = path_parts[1]
    issue_number = path_parts[3]
    
    # GitHub API URL for comments
    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'get_issue.py'
    }
    
    response = requests.get(api_url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"获取评论失败: {response.status_code} - {response.text}")
    
    return response.json()


def print_comments(comments):
    """
    打印评论
    
    Args:
        comments: 评论列表
    """
    if not comments:
        print("没有评论")
        return
    
    print(f"\n{'=' * 80}")
    print(f"评论 ({len(comments)} 条):")
    print(f"{'=' * 80}")
    
    for i, comment in enumerate(comments, 1):
        print(f"\n评论 #{i}:")
        print(f"作者: {comment['user']['login']}")
        print(f"时间: {comment['created_at']}")
        print(f"URL: {comment['html_url']}")
        print("-" * 40)
        print(comment['body'])
        print("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description='从 GitHub issue URL 获取 issue 文本并打印',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s https://github.com/owner/repo/issues/123
  %(prog)s https://github.com/owner/repo/issues/123 --comments
  %(prog)s https://github.com/owner/repo/issues/123 --json
        """
    )
    
    parser.add_argument(
        'issue_url',
        help='GitHub issue URL (例如: https://github.com/owner/repo/issues/123)'
    )
    
    parser.add_argument(
        '--comments',
        action='store_true',
        help='同时获取并打印评论'
    )
    
    parser.add_argument(
        '--json',
        action='store_true',
        help='以 JSON 格式输出原始数据'
    )
    
    parser.add_argument(
        '--body-only',
        action='store_true',
        help='只打印 issue 正文内容'
    )
    
    args = parser.parse_args()
    
    try:
        # 获取 issue 数据
        issue_data = get_issue_from_url(args.issue_url)
        
        if args.json:
            # 输出 JSON 格式
            import json
            print(json.dumps(issue_data, indent=2, ensure_ascii=False))
        elif args.body_only:
            # 只输出正文
            print(issue_data['body'] if issue_data['body'] else "")
        else:
            # 输出格式化信息
            print_issue_details(issue_data)
            
            # 如果需要评论
            if args.comments:
                comments = get_comments(args.issue_url)
                print_comments(comments)
                
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()