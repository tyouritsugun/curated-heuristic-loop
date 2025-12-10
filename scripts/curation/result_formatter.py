#!/usr/bin/env python3
"""
Result formatting functionality for curation output.
"""

import json
import io
from typing import Dict, List
import csv


class ResultFormatter:
    @staticmethod
    def format_results(results: List[Dict], format_type: str) -> str:
        """Format results in specified format."""
        if format_type == "table":
            # Table format
            if not results:
                return "No results found."

            header = f"{'Pending ID':<20} {'Anchor ID':<20} {'Score':<6} {'Bucket':<8} {'Category':<15} {'Section Mismatch':<8}"
            separator = "-" * len(header)

            table_rows = [header, separator]
            for result in results:
                row = f"{result['pending_id'][:19]:<20} {result['anchor_id'][:19]:<20} {result['score']:<6.3f} {result['bucket']:<8} {result['category']:<15} {str(result['section_mismatch']):<8}"
                table_rows.append(row)

            return "\n".join(table_rows)

        elif format_type == "json":
            return json.dumps(results, indent=2, ensure_ascii=False)

        elif format_type == "csv":
            if not results:
                return ""

            output = io.StringIO()

            fieldnames = [
                'pending_id', 'pending_type', 'anchor_id', 'anchor_type',
                'score', 'bucket', 'category', 'pending_title',
                'anchor_title', 'section_mismatch', 'id_collision_flag'
            ]

            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for result in results:
                writer.writerow(result)

            return output.getvalue()

        elif format_type == "spreadsheet":
            # Export to a format suitable for Google Sheets/Excel
            if not results:
                return ""

            output = io.StringIO()

            # Enhanced fieldnames for better spreadsheet visualization
            fieldnames = [
                'pending_id', 'pending_type', 'anchor_id', 'anchor_type',
                'score', 'bucket', 'category', 'pending_title',
                'anchor_title', 'section_mismatch', 'id_collision_flag',
                'recommendation', 'merge_group_id'
            ]

            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()

            # Track merge groups - items that should be merged together get the same group ID
            from collections import defaultdict
            merge_groups = defaultdict(list)
            group_counter = 1

            for result in results:
                # For high-similarity items (candidates for merging), group them
                if result['bucket'] == 'high':
                    # Create a group key based on the anchor ID to group all items
                    # that should merge into the same anchor
                    group_key = result['anchor_id']
                    if group_key not in merge_groups:
                        merge_groups[group_key] = f"GRP-{group_counter:03d}"
                        group_counter += 1

                    group_id = merge_groups[group_key]
                    recommendation = 'MERGE RECOMMENDED'
                else:
                    group_id = ''  # No group for non-merge items
                    if result['bucket'] == 'medium':
                        recommendation = 'REVIEW NEEDED'
                    elif result['bucket'] == 'low':
                        recommendation = 'MANUAL REVIEW'
                    else:
                        recommendation = 'NO ACTION'

                row = result.copy()
                row['recommendation'] = recommendation
                row['merge_group_id'] = group_id
                writer.writerow(row)

            return output.getvalue()