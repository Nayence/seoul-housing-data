# CDN devant le bucket du site.
#
# DEUX ROLES, et le second est le plus important :
#   1. Servir les fichiers depuis un point de presence proche du visiteur
#   2. Etre l'UNIQUE porte d'entree vers le bucket, qui reste ferme
#
# Un bucket S3 ouvert au public est une erreur classique : aucun controle
# de cache, aucune journalisation exploitable, aucune protection, et un
# risque permanent d'exposition involontaire. Avec CloudFront, le bucket
# n'autorise qu'un seul lecteur : cette distribution precise.

# L'Origin Access Control signe les requetes de CloudFront vers S3.
# Il remplace l'ancien Origin Access Identity, qui ne gere ni le
# chiffrement KMS ni les regions recentes.
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.project_name}-oac"
  description                       = "Acces CloudFront au bucket du site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Politiques gerees par AWS plutot que redefinies a la main : elles sont
# maintenues, testees, et leur nom documente l'intention.
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${var.project_name} — donnees locatives de Seoul"

  # PriceClass_200 couvre l'Europe, l'Amerique du Nord et l'Asie, mais pas
  # l'Amerique du Sud ni l'Oceanie. Le public vise est francophone et
  # coreen : payer pour Sao Paulo et Sydney n'aurait pas de sens.
  price_class = "PriceClass_200"

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "site-bucket"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "site-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]

    # Compression a la volee. Sur du JSON, le gain depasse souvent 80 % —
    # tes 1,9 Mo d'agregats descendent autour de 300 Ko transmis.
    compress = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  # Avec un OAC, une cle absente provoque un 403 et non un 404, puisque
  # le droit de lister le bucket n'est pas accorde. On retablit une
  # semantique correcte pour le visiteur.
  custom_error_response {
    error_code            = 403
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 60
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # Certificat par defaut de CloudFront, valable sur le domaine
    # *.cloudfront.net. Pour un domaine personnalise, il faudra un
    # certificat ACM cree dans la region us-east-1 — CloudFront etant un
    # service global, il n'accepte que les certificats de cette region.
    cloudfront_default_certificate = true
  }
}


# --- Ouverture du bucket a CloudFront uniquement --------------------------

data "aws_iam_policy_document" "site_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # La condition est essentielle : sans elle, N'IMPORTE QUELLE
    # distribution CloudFront, y compris celle d'un inconnu, pourrait
    # lire ton bucket. On restreint a cette distribution precise.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
}


output "site_url" {
  description = "URL publique du site."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "cloudfront_distribution_id" {
  description = "Identifiant de la distribution, pour invalider le cache."
  value       = aws_cloudfront_distribution.site.id
}
