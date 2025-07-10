#!/usr/bin/env python3
"""
Setup script for LocalStack resources for MCP testing

This script creates the necessary SNS topics and SQS queues for testing.
"""

import json
import sys
from shared import print_colored, setup_aws_clients, ensure_localstack_running


def setup_localstack():
    """Setup SNS topics and SQS queues in LocalStack"""
    try:
        print_colored("🔧 Setting up LocalStack resources...", "cyan")

        ensure_localstack_running()

        # Create AWS clients
        sqs_client, sns_client = setup_aws_clients()

        # Create SNS topics
        topics = [("mcp-requests", "Client requests to server"), ("mcp-response", "Server responses to client")]

        topic_arns = {}
        for topic_name, description in topics:
            try:
                response = sns_client.create_topic(Name=topic_name)
                topic_arn = response["TopicArn"]
                topic_arns[topic_name] = topic_arn
                print_colored(f"Created SNS topic: {topic_name}", "green")
            except Exception as e:
                print_colored(f"SNS topic {topic_name} may already exist: {e}", "yellow")
                # Try to get existing topic ARN
                try:
                    response = sns_client.create_topic(Name=topic_name)
                    topic_arns[topic_name] = response["TopicArn"]
                except:
                    pass

        # Create SQS queues
        queues = [
            ("mcp-processor", "Server request processing queue"),
            ("mcp-consumer", "Client response consumption queue"),
        ]

        queue_urls = {}
        for queue_name, description in queues:
            try:
                response = sqs_client.create_queue(
                    QueueName=queue_name,
                    Attributes={
                        "MessageRetentionPeriod": "1209600"  # 14 days
                    },
                )
                queue_url = response["QueueUrl"]
                queue_urls[queue_name] = queue_url
                print_colored(f"Created SQS queue: {queue_name}", "green")
            except Exception as e:
                print_colored(f"SQS queue {queue_name} may already exist: {e}", "yellow")
                # Try to get existing queue URL
                try:
                    response = sqs_client.get_queue_url(QueueName=queue_name)
                    queue_urls[queue_name] = response["QueueUrl"]
                except:
                    pass

        # Subscribe SQS queues to SNS topics
        subscriptions = [
            ("mcp-requests", "mcp-processor"),  # Client requests -> Server queue
            ("mcp-response", "mcp-consumer"),  # Server responses -> Client queue
        ]

        for topic_name, queue_name in subscriptions:
            try:
                topic_arn = topic_arns.get(topic_name, f"arn:aws:sns:us-east-1:000000000000:{topic_name}")
                queue_url = queue_urls.get(queue_name, f"http://localhost:4566/000000000000/{queue_name}")

                # Get queue attributes to get the ARN
                queue_attrs = sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
                queue_arn = queue_attrs["Attributes"]["QueueArn"]

                # Subscribe queue to topic
                sns_client.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)

                # Set queue policy to allow SNS to send messages
                policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
                        }
                    ],
                }

                sqs_client.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps(policy)})

                print_colored(f"Subscribed {queue_name} to {topic_name}", "green")

            except Exception as e:
                print_colored(f"Subscription {topic_name}->{queue_name} may already exist: {e}", "yellow")

        print_colored("\nLocalStack setup complete!", "green")
        return True

    except Exception as e:
        print_colored(f"❌ Setup failed: {e}", "red")
        return False


def cleanup_localstack():
    """Cleanup LocalStack resources"""
    try:
        print_colored("🧹 Cleaning up LocalStack resources...", "yellow")

        sqs_client, sns_client = setup_aws_clients()

        # Delete queues
        try:
            queues_response = sqs_client.list_queues()
            for queue_url in queues_response.get("QueueUrls", []):
                if "mcp-" in queue_url:
                    sqs_client.delete_queue(QueueUrl=queue_url)
                    queue_name = queue_url.split("/")[-1]
                    print_colored(f"🗑️  Deleted queue: {queue_name}", "yellow")
        except Exception as e:
            print_colored(f"⚠️  Error deleting queues: {e}", "yellow")

        # Delete topics
        try:
            topics_response = sns_client.list_topics()
            for topic in topics_response["Topics"]:
                if "mcp-" in topic["TopicArn"]:
                    sns_client.delete_topic(TopicArn=topic["TopicArn"])
                    topic_name = topic["TopicArn"].split(":")[-1]
                    print_colored(f"🗑️  Deleted topic: {topic_name}", "yellow")
        except Exception as e:
            print_colored(f"⚠️  Error deleting topics: {e}", "yellow")

        print_colored("✅ Cleanup complete!", "green")
        return True

    except Exception as e:
        print_colored(f"❌ Cleanup failed: {e}", "red")
        return False


def main():
    """Main entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        success = cleanup_localstack()
    else:
        success = setup_localstack()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
