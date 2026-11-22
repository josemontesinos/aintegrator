resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

resource "aws_subnet" "public" {
  for_each = { for az, cidr in zipmap(var.availability_zones, var.public_subnet_cidrs) : az => cidr }

  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${each.key}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = local.name }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = local.namespace_name
  vpc  = aws_vpc.main.id

  tags = { Name = local.namespace_name }
}
